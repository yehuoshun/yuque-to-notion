#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""刀4 离线集成测试：游标推进 / 安全阀 / 每篇落盘 / 分桶 / 指纹

手段：真跑 scripts/batch_migrate.py，但
  1) PATH 里放一个假 mcporter（冒充语雀 MCP）
  2) PYTHONPATH 里放 sitecustomize.py（拦掉 requests 与 time.sleep）
所以全程不联网、不碰真库。

用法：python3 tests/test_cursor.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, 'scripts')
sys.path.insert(0, SCRIPTS)
import state_store as st  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(('  ✅ ' if cond else '  ❌ ') + f'{name}' + (f' | {detail}' if detail else ''))


# ── 假 mcporter ──
FAKE_MCPORTER = '''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
# 真 CLI 形式：mcporter call <tool> --args <json> --output json
tool = args[1] if args and args[0] == 'call' else (args[0] if args else '')
a = {}
if '--args' in args:
    a = json.loads(args[args.index('--args') + 1])
if tool.endswith('yuque_web_get_doc'):
    if os.environ.get('FAKE_DOC_MODE') == 'error':
        print(json.dumps({'isError': True})); sys.exit(0)
    doc_id = str(a.get('id'))
    if doc_id in set((os.environ.get('FAKE_FAIL_IDS') or '').split(',')):
        print(json.dumps({'id': doc_id, 'title': 'T' + doc_id, 'slug': 's' + doc_id, 'body': '', 'word_count': 0}))
    else:
        print(json.dumps({'id': doc_id, 'title': 'T' + doc_id, 'slug': 's' + doc_id,
                          'body': '<p>正文' + doc_id + '</p>', 'word_count': 5}))
else:
    print(json.dumps({'data': []}))
'''

# ── sitecustomize：拦 requests + time.sleep ──
SITECUSTOMIZE = '''import json, os
class Resp:
    def __init__(self, code, body=None):
        self.status_code = code
        self._body = body if body is not None else {}
        self.text = json.dumps(self._body)
    def json(self):
        return self._body
_state = {'pages': 0, 'dedupe': 0}
def _route(method, url, **kw):
    if '/data_sources/' in url and url.rstrip('/').endswith('/query'):
        _state['dedupe'] += 1
        fa = int(os.environ.get('FAKE_DEDUP_FAIL_AFTER') or 0)
        if fa and _state['dedupe'] > fa:
            return Resp(400, {'code': 'validation_error', 'message': 'boom'})
        return Resp(200, {'results': [], 'has_more': False})
    if method == 'GET' and '/v1/databases/' in url:
        return Resp(200, {'id': 'db', 'data_sources': [{'id': 'ds-1', 'name': 'x'}]})
    if method == 'GET' and '/v1/data_sources/' in url:
        return Resp(200, {'id': 'ds-1', 'properties': {'原文链接': {'type': 'url'}}})
    if method == 'POST' and url.endswith('/v1/pages'):
        _state['pages'] += 1
        return Resp(200, {'id': 'page-%d' % _state['pages']})
    if method == 'PATCH' and '/markdown' in url:
        return Resp(200, {})
    if method == 'GET' and '/v1/blocks/' in url:
        return Resp(200, {'results': []})
    if method == 'PATCH' and '/v1/blocks/' in url:
        return Resp(200, {})
    if method == 'POST' and '/v1/file_uploads' in url:
        return Resp(200, {'id': 'fid', 'upload_url': 'https://up.invalid/x'})
    return Resp(200, {})
class _R:
    get = staticmethod(lambda url, **kw: _route('GET', url, **kw))
    post = staticmethod(lambda url, **kw: _route('POST', url, **kw))
    patch = staticmethod(lambda url, **kw: _route('PATCH', url, **kw))
import requests
requests.get, requests.post, requests.patch = _R.get, _R.post, _R.patch
import time as _t
_t.sleep = lambda *a, **k: None
'''


class Env:
    """一套隔离的跑批环境。"""

    def __init__(self, n_docs=25):
        self.root = tempfile.mkdtemp(prefix='y2n-cur-')
        self.state = os.path.join(self.root, 'state')
        self.bindir = os.path.join(self.root, 'bin')
        self.sitedir = os.path.join(self.root, 'site')
        for d in (self.state, self.bindir, self.sitedir):
            os.makedirs(d, exist_ok=True)
        p = os.path.join(self.bindir, 'mcporter')
        with open(p, 'w') as f:
            f.write(FAKE_MCPORTER)
        os.chmod(p, 0o755)
        with open(os.path.join(self.sitedir, 'sitecustomize.py'), 'w') as f:
            f.write(SITECUSTOMIZE)
        self.entries = [{'doc_id': 1000 + i, 'title': f'文档{i}', 'slug': f's{i}',
                         'source': '其他', 'category': '其他'} for i in range(n_docs)]
        self.write_entries()

    def write_entries(self, entries=None):
        with open(os.path.join(self.state, 'yuque_entries.json'), 'w') as f:
            json.dump(entries if entries is not None else self.entries, f, ensure_ascii=False)

    def seed_results(self, recs):
        st.save_results(os.path.join(self.state, 'prev_results.json'), recs)

    def seed_progress(self, jobs):
        st.save_progress(os.path.join(self.state, 'progress.json'), {'version': 1, 'jobs': jobs})

    def run(self, *args, env_extra=None, book_id='TESTBOOK'):
        env = dict(os.environ)
        env['PATH'] = self.bindir + os.pathsep + env['PATH']
        env['PYTHONPATH'] = self.sitedir + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
        env['YUQUE2NOTION_STATE_DIR'] = self.state
        env['NOTION_TOKEN'] = 'fake-token'
        env.update(env_extra or {})
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, 'batch_migrate.py'),
                            '--state-dir', self.state, '--book-id', book_id, *args],
                           capture_output=True, text=True, env=env, cwd=REPO, timeout=180)
        return r

    def cursor(self, book_id='TESTBOOK'):
        prog = st.load_progress(os.path.join(self.state, 'progress.json'))
        job = st.get_job(prog, st.job_key(book_id, _DB_ID))
        return job

    def results(self):
        return st.load_results(os.path.join(self.state, 'prev_results.json'))

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


_DB_ID = None


def db_id():
    sys.path.insert(0, SCRIPTS)
    import config
    return config.NOTION_DATABASE_ID


_DB_ID = db_id()

print('== 1. 全 SKIP 批：游标必须推进（旧代码卡死在这里） ==')
e = Env()
e.seed_results([{'idx': i, 'status': 'OK', 'doc_id': 1000 + i, 'title': f'文档{i}'} for i in range(10)])
r = e.run()
job = e.cursor()
check('exit 0', r.returncode == 0, r.stderr[-200:] if r.returncode else '')
check('游标推进到 10（不再卡在 0）', job and job['cursor'] == 10, str(job and job['cursor']))
check('未重复抓取语雀（SKIP 在 fetch 之前）', 'BATCH: 0-9/25' in r.stdout, '')
check('last_batch 记账正确', job and job['last_batch'] == {'ok': 0, 'partial': 0, 'skip': 10, 'fail': 0},
      str(job and job['last_batch']))
e.cleanup()

print('\n== 2. 混合批：无错位，下批起点恰好 start+len(batch) ==')
e = Env()
e.seed_results([{'idx': i, 'status': 'OK', 'doc_id': 1000 + i, 'title': f'文档{i}'} for i in (0, 2, 4)])
r = e.run()
job = e.cursor()
check('exit 0', r.returncode == 0, '')
check('游标 = 10（窗口整批前进）', job and job['cursor'] == 10, str(job and job['cursor']))
check('第一批处理了 10 篇（doc_id 1000..1009）',
      {str(x['doc_id']) for x in e.results()} == {str(1000 + i) for i in range(10)},
      str(len(e.results())))
r2 = e.run()
job2 = e.cursor()
check('第二批从 10 开始', job2 and job2['cursor'] == 20, str(job2 and job2['cursor']))
check('第二批是 doc_id 1010..1019（无错位/无重复）',
      'BATCH: 10-19/25' in r2.stdout, [l for l in r2.stdout.splitlines() if l.startswith('BATCH')][:1])
e.cleanup()

print('\n== 3. 整批全废：游标回滚到批首 + exit 2 ==')
e = Env()
r = e.run(env_extra={'FAKE_DOC_MODE': 'error'})
job = e.cursor()
check('exit 2', r.returncode == 2, str(r.returncode))
check('游标回滚到 0（未推进）', job and job['cursor'] == 0, str(job and job['cursor']))
check('FATAL 提示风控/网络', '疑似语雀风控或网络故障' in r.stdout, '')
check('10 篇全进重试队列', len(st.load_queue(os.path.join(e.state, 'retry_queue.json'))) == 10,
      str(len(st.load_queue(os.path.join(e.state, 'retry_queue.json')))))
check('last_batch.fail = 10', job and job['last_batch'].get('fail') == 10, str(job and job['last_batch']))

print('\n== 4. 重试队列消费：--mode retry-failed 清空队列 ==')
r2 = e.run('--mode', 'retry-failed')
q = st.load_queue(os.path.join(e.state, 'retry_queue.json'))
check('重试后队列清空', q == [], str(len(q)))
check('主游标未被 retry-failed 影响', e.cursor()['cursor'] == 0, str(e.cursor()['cursor']))
e.cleanup()

print('\n== 5. 每篇落盘：中途崩溃，游标停在最后一篇已完成处 ==')
e = Env()
r = e.run(env_extra={'FAKE_DEDUP_FAIL_AFTER': '6'})
job = e.cursor()
check('exit 2', r.returncode == 2, str(r.returncode))
check('游标 = 8（前 6 篇完成 + 2 篇去重失败已记账）', job and job['cursor'] == 8, str(job and job['cursor']))
check('去重失败进了重试队列', len(st.load_queue(os.path.join(e.state, 'retry_queue.json'))) >= 2, '')
r2 = e.run()   # 不设 FAKE_DEDUP_FAIL_AFTER → 恢复正常
check('续跑从第 9 篇开始（不重头来）', 'BATCH: 8-17/25' in r2.stdout,
      [l for l in r2.stdout.splitlines() if l.startswith('BATCH')][:1])
e.cleanup()

print('\n== 6. 清单指纹：改了清单不许静默沿用游标 ==')
e = Env()
e.run()   # 建 job，游标 10
changed = list(reversed(e.entries))      # 顺序变了
e.write_entries(changed)
r = e.run()
check('指纹不符 → exit 2', r.returncode == 2, str(r.returncode))
check('提示加 --reset-cursor', '--reset-cursor' in r.stdout, '')
r2 = e.run('--reset-cursor', '--dry-run')
check('--reset-cursor 后游标归零', 'cursor=0' in r2.stdout.replace('游标=0', 'cursor=0') or '游标=0' in r2.stdout,
      [l for l in r2.stdout.splitlines() if 'dry-run] 任务' in l][:1])
e.cleanup()

print('\n== 7. 换书分桶：不同 book 各自游标，互不覆盖 ==')
e = Env()
e.run(book_id='BOOKA')
e.run(book_id='BOOKB')
prog = st.load_progress(os.path.join(e.state, 'progress.json'))
jobs = prog.get('jobs', {})
check('两个任务并存', len(jobs) == 2, str(list(jobs.keys())))
check('各自游标独立为 10', all(j['cursor'] == 10 for j in jobs.values()), str({k: v['cursor'] for k, v in jobs.items()}))
e.cleanup()

print('\n== 8. state_store 单元：合并优先级 / 游标单调 / 队列幂等 ==')
merged = st.merge_results([
    {'doc_id': 'a', 'status': 'FAIL'},
    {'doc_id': 'a', 'status': 'OK', 'page_ids': {'0': 'p1'}},
    {'doc_id': 'a', 'status': 'PARTIAL'},          # 低优先级不得覆盖 OK
    {'doc_id': 'b', 'status': 'FAIL'},
    {'doc_id': 'b', 'status': 'RATE_LIMITED'},     # 同级/更低 → 保留 FAIL
])
by = {r['doc_id']: r for r in merged}
check('OK 不被 PARTIAL 降级', by['a']['status'] == 'OK', by['a']['status'])
check('page_ids 保留', by['a'].get('page_ids') == {'0': 'p1'}, str(by['a'].get('page_ids')))
check('FAIL 不被 RATE_LIMITED 降级', by['b']['status'] == 'FAIL', by['b']['status'])

prog = {}
st.set_job(prog, 'k', st.new_job('b', 'd', 3, 'sha1:x'))
st.update_cursor(prog, 'k', 5)
try:
    st.update_cursor(prog, 'k', 3)
    check('游标回退被拒', False, '居然允许了')
except ValueError:
    check('游标回退被拒', True, '')
st.update_cursor(prog, 'k', 3, allow_decrease=True)
check('显式 allow_decrease 可回滚', st.get_job(prog, 'k')['cursor'] == 3, '')

qs_root = tempfile.mkdtemp(prefix='y2n-q-')
qp = os.path.join(qs_root, 'q.json')
st.queue_add(qp, ['a', 'b', 'a'])
check('队列去重', st.load_queue(qp) == ['a', 'b'], str(st.load_queue(qp)))
st.queue_remove(qp, 'a')
st.queue_remove(qp, 'zz')
check('队列移除幂等', st.load_queue(qp) == ['b'], str(st.load_queue(qp)))
st.save_queue(qp, ['x', 'y'])
check('save_queue 覆盖写', st.load_queue(qp) == ['x', 'y'], str(st.load_queue(qp)))
shutil.rmtree(qs_root, ignore_errors=True)

print('\n== 9. 指纹函数本身 ==')
f1 = st.entries_fingerprint([{'doc_id': 1, 'title': 'x'}, {'doc_id': 2, 'title': 'y'}])
f2 = st.entries_fingerprint([{'doc_id': 2, 'title': 'y'}, {'doc_id': 1, 'title': 'x'}])
f3 = st.entries_fingerprint([{'doc_id': 1, 'title': 'x'}, {'doc_id': 2, 'title': 'y'}])
check('顺序不同 → 指纹不同', f1 != f2, f'{f1} vs {f2}')
check('相同清单 → 指纹相同', f1 == f3, f1)

print('\n== 10. --mode new：清单变了也不卡指纹，直接全量重扫 ==')
e = Env()
e.run()   # 建 job：游标 10，结果 10 条（doc_id 1000..1009）
changed = list(reversed(e.entries))      # 顺序变 → 指纹变
n_changed = len(changed)
e.write_entries(changed)
r = e.run('--mode', 'new')
job = e.cursor()
check('exit 0（不再被指纹卡死）', r.returncode == 0, r.stderr[-200:] if r.returncode else '')
check('从 0 开始全量重扫', 'BATCH: 0-9/25' in r.stdout,
      [l for l in r.stdout.splitlines() if l.startswith('BATCH')][:1])
check('指纹更新为当前清单',
      job and job['entries_fingerprint'] == st.entries_fingerprint(changed),
      str(job and job['entries_fingerprint']))
check('旧账清空、按新清单重跑（doc_id 1015..1024）',
      {str(x['doc_id']) for x in e.results()} == {str(1000 + i) for i in range(15, 25)},
      str(sorted({x['doc_id'] for x in e.results()})))
check('重试队列保持空', st.load_queue(os.path.join(e.state, 'retry_queue.json')) == [], '')
e.cleanup()

print('\n== 11. --mode new --dry-run：零副作用预览 ==')
e = Env()
e.run()   # 建 job：游标 10，结果 10 条
before_prog = open(os.path.join(e.state, 'progress.json')).read()
r = e.run('--mode', 'new', '--dry-run')
check('exit 0', r.returncode == 0, r.stderr[-200:] if r.returncode else '')
check('预览从 0 开始', 'BATCH: 0-9/25' in r.stdout,
      [l for l in r.stdout.splitlines() if l.startswith('BATCH')][:1])
check('prev_results.json 原样保留（10 条）', len(e.results()) == 10, str(len(e.results())))
check('progress.json 未被改写', open(os.path.join(e.state, 'progress.json')).read() == before_prog, '')
e.cleanup()

print('\n== 12. 真空正文文档：不再误判 RATE_LIMITED，直接 SKIP ==')
e = Env()
r = e.run(env_extra={'FAKE_FAIL_IDS': '1000'})   # doc 1000 空 body + word_count=0
res = e.results()
check('exit 0', r.returncode == 0, r.stderr[-200:] if r.returncode else '')
check('空文档 SKIP（R8_空body）', bool(res) and res[0]['status'] == 'SKIP', str(res and res[0]['status']))
check('没进重试队列', st.load_queue(os.path.join(e.state, 'retry_queue.json')) == [], '')
check('游标推进（不卡死）', e.cursor() and e.cursor()['cursor'] == 10,
      str(e.cursor() and e.cursor()['cursor']))
e.cleanup()

print(f'\n{"=" * 52}\n通过 {len(PASS)} / 失败 {len(FAIL)}')
for x in FAIL:
    print('  ❌ ' + x)
sys.exit(1 if FAIL else 0)
