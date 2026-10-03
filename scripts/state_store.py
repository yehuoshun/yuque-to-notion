#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""状态存取：任务进度（按 book:db 分桶）、结果合并、重试队列。

设计原则：把「扫到哪」和「每篇的账」分开管。
- progress.json  → 只管游标（扫到哪），按 book_id:notion_db_id 分桶，带清单指纹
- results.json   → 管每篇的账，按 doc_id 合并，状态取优先级最高
- retry_queue.json → 重试队列，逐篇消费，不干扰主游标
"""
import hashlib
import json
import os
from datetime import datetime

# OK > PARTIAL > FAIL > RATE_LIMITED；SKIP 视为最低（中间态，可被更高状态覆盖）
STATUS_PRIORITY = {'RATE_LIMITED': 0, 'FAIL': 1, 'PARTIAL': 2, 'OK': 3}


def now_iso():
    return datetime.now().strftime('%Y-%m-%dT%H:%M:%S')


def entries_fingerprint(entries):
    """清单指纹：条数 + 每条的 doc_id/title。清单顺序变了就不匹配。"""
    h = hashlib.sha1()
    h.update(str(len(entries)).encode())
    for e in entries:
        h.update(str(e.get('doc_id', '')).encode())
        h.update(str(e.get('title', '')).encode())
    return 'sha1:' + h.hexdigest()[:16]


def job_key(book_id, notion_db_id):
    return f'{book_id}:{notion_db_id}'


def _read_json(path, default):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, data):
    tmp = path + '.tmp'
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(tmp, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ── progress.json ──

def load_progress(path):
    return _read_json(path, {})


def save_progress(path, data):
    _write_json(path, data)


def get_job(progress, key):
    return (progress.get('jobs') or {}).get(key)


def set_job(progress, key, job):
    progress.setdefault('version', 1)
    progress.setdefault('jobs', {})[key] = job
    return progress


def new_job(book_id, notion_db_id, entries_count, fingerprint, cursor=0):
    return {
        'book_id': str(book_id), 'notion_db_id': str(notion_db_id),
        'entries_count': entries_count, 'entries_fingerprint': fingerprint,
        'cursor': int(cursor), 'updated_at': now_iso(), 'last_batch': {},
    }


def update_cursor(progress, key, cursor, last_batch=None, allow_decrease=False):
    """更新游标。默认只增不减（防逻辑回归）；回滚必须显式 allow_decrease。"""
    job = get_job(progress, key)
    if job is None:
        raise KeyError(f'job 不存在: {key}')
    old = int(job.get('cursor', 0))
    cursor = int(cursor)
    if cursor < old and not allow_decrease:
        raise ValueError(f'游标不允许回退 {old} -> {cursor}（如需回滚请用 allow_decrease=True）')
    job['cursor'] = cursor
    job['updated_at'] = now_iso()
    if last_batch is not None:
        job['last_batch'] = last_batch
    return job


# ── results.json ──

def merge_results(records):
    """同 doc_id 合并成一条：状态取优先级最高，同级取最新；page_ids 缺失时沿用旧的。
    返回按首次出现顺序排列的列表。"""
    order, best = [], {}
    for r in records:
        did = str(r.get('doc_id', ''))
        if not did:
            continue
        if did not in best:
            best[did] = r
            order.append(did)
            continue
        cur, new = best[did], r
        cp = STATUS_PRIORITY.get(cur.get('status'), -1)
        np_ = STATUS_PRIORITY.get(new.get('status'), -1)
        if np_ >= cp:  # 同级也替换 → 取最新
            if new.get('page_ids') is None and cur.get('page_ids'):
                new = dict(new)
                new['page_ids'] = cur['page_ids']
            if not new.get('detail') and cur.get('detail'):
                new = dict(new)
                new['detail'] = cur['detail']
            best[did] = new
    return [best[d] for d in order]


def load_results(path):
    return _read_json(path, [])


def save_results(path, recs):
    _write_json(path, recs)


def upsert_result(path, rec):
    """写入一条结果（同 doc_id 合并），返回合并后的全量。"""
    recs = merge_results(load_results(path) + [rec])
    save_results(path, recs)
    return recs


# ── retry_queue.json ──

def load_queue(path):
    return _read_json(path, [])


def save_queue(path, doc_ids):
    """覆盖写队列（--mode new 重置用）。"""
    q = [str(x) for x in doc_ids]
    _write_json(path, q)
    return q


def queue_add(path, doc_ids):
    q = load_queue(path)
    seen = {str(x) for x in q}
    for d in doc_ids:
        d = str(d)
        if d and d not in seen:
            q.append(d)
            seen.add(d)
    _write_json(path, q)
    return q


def queue_remove(path, doc_id):
    q = [x for x in load_queue(path) if str(x) != str(doc_id)]
    _write_json(path, q)
    return q