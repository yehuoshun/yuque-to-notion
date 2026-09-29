import base64, subprocess, json, urllib.parse, re, time, sys, os
import requests
from lake_convert import lake_to_markdown, count_unsupported_cards
from cli import load_settings, ConfigError, ensure_state_dir
import state_store as st

# ── 生效配置（命令行 > 环境变量 > config.py），全部从 cli 拿 ──
S = load_settings(prog='batch_migrate')
MODE = S.mode
NOTION_DATABASE_ID = S.notion_db_id
NOTION_DATA_SOURCE_ID = S.notion_ds_id
NOTION_DEDUP_PROPERTY = S.dedup_property
NOTION_TOKEN = S.notion_token
DEDUP_QUERY_RETRIES = S.dedup_query_retries
DEDUP_MAX_CONSECUTIVE_FAILURES = S.dedup_max_consecutive_failures
DATA_FILE = S.entries_file
PROGRESS_FILE = S.progress_file
PREV_RESULTS_FILE = S.prev_results_file
PROGRESS_JSON = os.path.join(S.state_dir, 'progress.json')
RETRY_QUEUE_FILE = os.path.join(S.state_dir, 'retry_queue.json')
MAX_CONTENT_CHARS = S.max_content_chars
BATCH_SIZE = S.batch_size
YUQUE_BOOK_ID = S.book_id
YUQUE_NAMESPACE = S.namespace
YUQUE_USER = S.user
MCP_CWD = S.mcp_cwd

try:
    ensure_state_dir(S)
except ConfigError as e:
    print(f'[FATAL] {e}')
    sys.exit(2)

NOTION_HEADERS = {
    'Authorization': f'Bearer {NOTION_TOKEN}',
    'Notion-Version': '2026-03-11',
    'Content-Type': 'application/json'
}

# ── Notion 目标库探活：data source 解析 + 判重字段校验 ──
class NotionInitError(RuntimeError):
    pass

_DS_ID = None

def resolve_data_source_id():
    """解析并校验目标 data source。任何一步不对就抛 NotionInitError，不静默降级。
    原因：2025-09 之后 Notion 把 database 下的数据源拆成 data source，
    查询必须打 /v1/data_sources/{id}，老的 /v1/databases/{id}/query 已 400。"""
    global _DS_ID
    if _DS_ID:
        return _DS_ID
    r = requests.get(f'https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}',
                     headers=NOTION_HEADERS, timeout=30)
    if r.status_code != 200:
        raise NotionInitError(f'目标库不可访问 {r.status_code}: {r.text[:200]}')
    ds_list = r.json().get('data_sources') or []
    if NOTION_DATA_SOURCE_ID:
        _DS_ID = NOTION_DATA_SOURCE_ID          # 显式指定优先
    elif len(ds_list) == 1:
        _DS_ID = ds_list[0]['id']               # 单源自动取
    else:
        raise NotionInitError(
            f'库下有 {len(ds_list)} 个 data source，请在 config 显式指定 NOTION_DATA_SOURCE_ID')
    r2 = requests.get(f'https://api.notion.com/v1/data_sources/{_DS_ID}',
                      headers=NOTION_HEADERS, timeout=30)
    if r2.status_code != 200:
        raise NotionInitError(f'data source 不可访问 {r2.status_code}: {r2.text[:200]}')
    props = r2.json().get('properties') or {}
    if props.get(NOTION_DEDUP_PROPERTY, {}).get('type') != 'url':
        raise NotionInitError(f'data source 缺少 url 类型属性「{NOTION_DEDUP_PROPERTY}」')
    print(f'[notion] 目标 data source: {_DS_ID} | 判重字段: {NOTION_DEDUP_PROPERTY}')
    return _DS_ID

def create_notion_page(props):
    r = requests.post('https://api.notion.com/v1/pages',
        headers=NOTION_HEADERS,
        json={'parent': {'database_id': NOTION_DATABASE_ID}, 'properties': props}, timeout=60)
    if r.status_code in (200, 201):
        return r.json()
    print(f"    [notion] 创建失败 {r.status_code}: {r.text[:200]}")
    return None

def update_page_markdown(page_id, markdown):
    """写 markdown 到页面。返回 HTTP 状态码（int），异常返回 None。"""
    try:
        r = requests.patch(f'https://api.notion.com/v1/pages/{page_id}/markdown',
            headers=NOTION_HEADERS,
            json={'type': 'replace_content', 'replace_content': {'new_str': markdown, 'allow_deleting_content': True}},
            timeout=120)
        if r.status_code not in (200, 201):
            print(f"    [notion] markdown 写入失败 {r.status_code}: {r.text[:200]}")
        return r.status_code
    except Exception as e:
        print(f"    [notion] markdown 写入异常 {e}")
        return None

# 图片上传：逐张重试 + 节流（实测连发不会 429，但大图文档不该硬怼）
IMG_RETRIES = 2        # 429/5xx 重试次数
IMG_RETRY_WAIT = 1.5    # 重试基础等待（退避乘 2^n）
IMG_THROTTLE = 0.2      # 每张间隔（秒）

def notion_upload_images(image_srcs):
    """原生上传 base64 图片到 Notion。
    返回 (upload_map, failed_list)：failed 里每项是 '索引:阶段:原因'。"""
    result = {}
    failed = []
    for i, (src, link, ext) in enumerate(image_srcs):
        if not src or not src.startswith('data:image/'):
            continue
        b64 = src.split(',', 1)[1] if ',' in src else src
        try:
            raw = base64.b64decode(b64)
        except Exception:
            failed.append(f'{i}:decode:b64')
            continue
        if not raw:
            failed.append(f'{i}:decode:empty')
            continue

        # 创建上传会话（重试）
        r1 = None
        for attempt in range(IMG_RETRIES + 1):
            try:
                r1 = requests.post('https://api.notion.com/v1/file_uploads',
                    headers=NOTION_HEADERS,
                    json={'mode': 'single_part', 'filename': f'img_{i}.{ext}', 'content_type': f'image/{ext}'},
                    timeout=60)
            except Exception as e:
                r1 = None
            if r1 is not None and r1.status_code in (200, 201):
                break
            if attempt < IMG_RETRIES:
                time.sleep(IMG_RETRY_WAIT * (2 ** attempt))
        if r1 is None:
            failed.append(f'{i}:create:exc')
            continue
        if r1.status_code not in (200, 201):
            failed.append(f'{i}:create:{r1.status_code}')
            continue

        # 传字节（重试）
        fu = r1.json()
        r2 = None
        for attempt in range(IMG_RETRIES + 1):
            try:
                r2 = requests.post(fu['upload_url'],
                    headers={'Authorization': f'Bearer {NOTION_TOKEN}', 'Notion-Version': '2026-03-11'},
                    files={'file': (f'img_{i}.{ext}', raw, f'image/{ext}')}, timeout=120)
            except Exception:
                r2 = None
            if r2 is not None and r2.status_code in (200, 201):
                break
            if attempt < IMG_RETRIES:
                time.sleep(IMG_RETRY_WAIT * (2 ** attempt))
        if r2 is None:
            failed.append(f'{i}:upload:exc')
            continue
        if r2.status_code not in (200, 201):
            failed.append(f'{i}:upload:{r2.status_code}')
            continue

        placeholder = f'https://n2m.local/img_{i}.{ext}'
        result[placeholder] = fu['id']
        time.sleep(IMG_THROTTLE)
    return result, failed

def notion_attach_images(page_id, upload_map, markdown):
    """读回页面 blocks，把占位图 PATCH 成 file_upload 类型。
    返回 (attached, failed_list)。"""
    try:
        r = requests.get(f'https://api.notion.com/v1/blocks/{page_id}/children',
            headers=NOTION_HEADERS, timeout=60)
    except Exception as e:
        print(f"    [img] 读 blocks 异常 {e}")
        return 0, [f'read:exc']
    if r.status_code != 200:
        print(f"    [img] 读 blocks 失败 {r.status_code}")
        return 0, [f'read:{r.status_code}']
    attached = 0
    failed = []
    for b in r.json().get('results', []):
        if b.get('type') != 'image':
            continue
        ext = (b.get('image') or {}).get('external') or {}
        url = ext.get('url', '')
        if url not in upload_map:
            continue
        fuid = upload_map[url]
        try:
            r2 = requests.patch(f"https://api.notion.com/v1/blocks/{b['id']}", headers=NOTION_HEADERS,
                json={'image': {'file_upload': {'id': fuid}}}, timeout=60)
        except Exception:
            r2 = None
        if r2 is not None and r2.status_code in (200, 201):
            attached += 1
        else:
            code = r2.status_code if r2 is not None else 'exc'
            print(f"    [img] PATCH 失败 {code}: {r2.text[:150] if r2 is not None else ''}")
            failed.append(f'{url}:patch:{code}')
    return attached, failed

URL_PATTERN = r'https?://[^\s"<>\')\]]+'

# 拆分阈值（Notion Markdown API 隐式大小限制）
# MAX_CONTENT_CHARS / MIN_CHUNK_BYTES 均来自 cli（config.py 是唯一来源）
MIN_CHUNK_BYTES = S.split_min_chunk   # 章节小于此并入上一节
SPLIT_MIN_LEVEL = S.split_min_level
SPLIT_MAX_LEVEL = S.split_max_level

def mcpcall(tool, args, retries=3):
    cmd = ['mcporter', 'call', tool, '--args', json.dumps(args), '--output', 'json']
    # 必须固定 cwd：mcporter 靠 cwd 找 MCP server 配置（可用 --mcp-cwd / OPENCLAW_WORKSPACE 覆盖）
    for attempt in range(retries):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=45, cwd=MCP_CWD)
            data = json.loads(r.stdout)
            # 判断错误：isError 标志 / error 键（含 Unknown MCP server）/ status>=400
            if isinstance(data, dict):
                if data.get('isError'):
                    print(f'    [mcp] {tool} isError, 重试 {attempt+1}/{retries}...')
                    time.sleep(3)
                    continue
                if 'error' in data:
                    print(f'    [mcp] {tool} error={str(data["error"])[:80]}, 重试 {attempt+1}/{retries}...')
                    time.sleep(3)
                    continue
                if data.get('status') and isinstance(data.get('status'), (int, float)) and data['status'] >= 400:
                    print(f'    [mcp] {tool} status={data["status"]}, 重试 {attempt+1}/{retries}...')
                    time.sleep(3)
                    continue
            return data
        except Exception as e:
            print(f'    [mcp] {tool} 异常 {e}, 重试 {attempt+1}/{retries}...')
            time.sleep(3)
    return None

def fetch_doc_with_retry(entry, book_id, max_retries=2):
    """取单篇文档，body 空时渐进退避。返回 (result_dict, failure_reason)"""
    for attempt in range(max_retries + 1):
        result = mcpcall('yuque-mcp.yuque_web_get_doc', {
            'id': str(entry['doc_id']), 'book_id': book_id
        })
        if result is None:
            if attempt < max_retries:
                print(f"    [retry] 网络异常, 等 10s 后重试 ({attempt+1}/{max_retries})")
                time.sleep(10)
            continue
        if result.get('body'):
            return result, None
        if attempt == 0:
            print(f"    [retry] body 空（疑似风控）, 等 30s 后重试")
            time.sleep(30)
        else:
            print(f"    [retry] body 仍空, 等 60s 后重试 ({attempt+1}/{max_retries})")
            time.sleep(60)
    return None, 'RATE_LIMITED'

def save_result(result_json, idx=None):
    """落盘一篇结果（同 doc_id 合并），并维护游标/重试队列。
    idx 给了就同时推进游标（每篇落盘，崩溃最多重来 1 篇）。"""
    st.upsert_result(PREV_RESULTS_FILE, result_json)
    doc_id = str(result_json.get('doc_id', ''))
    if MODE == 'retry-failed':
        st.queue_remove(RETRY_QUEUE_FILE, doc_id)
    if result_json.get('status') in ('FAIL', 'RATE_LIMITED'):
        st.queue_add(RETRY_QUEUE_FILE, [doc_id])
    if idx is not None and MODE != 'retry-failed':
        try:
            st.update_cursor(progress, JOB_KEY, idx + 1)
        except ValueError as e:
            print(f'    [progress] ⚠️ {e}')
        st.save_progress(PROGRESS_JSON, progress)

def doc_body_precheck(result, entry):
    """文档预检：R1 二进制 / R2 数据库dump / R8 无意义内容。返回 (pass: bool, reason: str|None)"""
    body = result.get('body', '')
    word_count = result.get('word_count', 0)
    if not body:
        return False, 'R8_空body'

    total = len(body)
    readable = len(re.findall(r'[\u4e00-\u9fff\w]{2,}', body))
    has_card = '<card' in body
    # R1: 二进制 — 可读文本比例 < 5%，且无 lake card 标签
    if not has_card and readable < max(5, total * 0.05) and total > 500:
        return False, 'R1_二进制'
    # R2: 数据库 dump — 10+ 条 SQL 模式
    sql_lines = len(re.findall(r'(CREATE\s+TABLE|INSERT\s+INTO|DROP\s+TABLE|ALTER\s+TABLE|'
                                r'CREATE\s+INDEX|TRUNCATE\s+TABLE)', body, re.I))
    if sql_lines > 10:
        return False, 'R2_数据库dump'
    # R8: 无意义内容 — 可读字符 < 10 且 word_count < 5，且无 card
    if not has_card and readable < 10 and word_count < 5:
        return False, 'R8_无意义内容'
    return True, None


def check_notion_exists(yuque_url):
    """查 Notion 是否有 判重字段=yuque_url 的记录。
    True=已存在。查询失败一律抛 NotionInitError —— 绝不静默当成“不存在”。"""
    ds_id = resolve_data_source_id()
    body = {'filter': {'property': NOTION_DEDUP_PROPERTY, 'url': {'equals': yuque_url}},
            'page_size': 1}
    for attempt in range(DEDUP_QUERY_RETRIES):
        r = requests.post(f'https://api.notion.com/v1/data_sources/{ds_id}/query',
                          headers=NOTION_HEADERS, json=body, timeout=30)
        if r.status_code == 200:
            return len(r.json().get('results', [])) > 0
        if r.status_code == 429 or r.status_code >= 500:
            wait = 3 * (2 ** attempt)
            print(f'    [notion] 去重查询 {r.status_code}，{wait}s 后重试 ({attempt+1}/{DEDUP_QUERY_RETRIES})')
            time.sleep(wait)
            continue
        raise NotionInitError(f'去重查询失败 {r.status_code}: {r.text[:200]}')
    raise NotionInitError(f'去重查询连续失败 {DEDUP_QUERY_RETRIES} 次（限流/5xx）')


# ── 标题/围栏识别 ──
HEADING_RE = re.compile(r'^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$')
FENCE_RE = re.compile(r'^\s{0,3}(`{3,}|~{3,})(.*)$')


def _fence_open(line):
    """这行是不是围栏开始（返回标记串，如 '```' / '~~~~'）；不是返回 None"""
    m = FENCE_RE.match(line)
    if not m:
        return None
    marker = m.group(1)
    if marker[0] == '`' and '`' in m.group(2):   # 行内反引号，不是围栏
        return None
    return marker


def _fence_close(line, marker):
    """这行是不是 marker 对应围栏的结束（长度≥开护栏、后面只有空白）"""
    m = FENCE_RE.match(line)
    if not m:
        return False
    return m.group(1)[0] == marker[0] and len(m.group(1)) >= len(marker) and not m.group(2).strip()


def _heading(line, min_level, max_level):
    """这行是不是目标层级的标题（返回标题文本）；不是返回 None"""
    m = HEADING_RE.match(line)
    if not m:
        return None
    lvl = len(m.group(1))
    if min_level <= lvl <= max_level:
        return m.group(2).strip()
    return None


def split_markdown_by_headings(markdown, threshold=MAX_CONTENT_CHARS, min_chunk=MIN_CHUNK_BYTES,
                               min_level=SPLIT_MIN_LEVEL, max_level=SPLIT_MAX_LEVEL):
    """按标题拆分长 markdown。
    保证：
      1. 不切进代码围栏（``` / ~~~ 配对识别，围栏内的 ## 不当标题）
      2. 首个标题之前的内容独立成第 0 片（不丢引言）
      3. 只认 min_level~max_level 级标题
      4. 单片超阈值 → 在围栏外的行边界二次切分；切在代码块里则闭合+重开围栏，内容一字不丢
    返回 [(section_title|None, content), ...]
    """
    if not markdown:
        return [(None, '')]
    if len(markdown) <= threshold:
        return [(None, markdown)]

    sections = _split_sections(markdown, min_level, max_level)
    merged = _merge_small_sections(sections, min_chunk)

    out = []
    for title, content in merged:
        parts = _split_oversize(content, threshold)
        base = title or '（无标题）'
        for i, part in enumerate(parts):
            t = title if i == 0 else f'{base}·续{i + 1}'
            out.append((t, part))
    if not out:
        return [(None, markdown)]
    return out


def _split_sections(markdown, min_level, max_level):
    """单遍扫描，带围栏状态机，按目标层级标题断章。返回 [(title, content)]"""
    sections = []
    cur_title, cur_lines = None, []
    fence = None
    for ln in markdown.split('\n'):
        if fence is None:
            m = _fence_open(ln)
            if m:
                fence = m
                cur_lines.append(ln)
                continue
            h = _heading(ln, min_level, max_level)
            if h:
                sections.append((cur_title, '\n'.join(cur_lines).strip()))
                cur_title, cur_lines = h, [ln]
                continue
            cur_lines.append(ln)
        else:
            cur_lines.append(ln)
            if _fence_close(ln, fence):
                fence = None
    sections.append((cur_title, '\n'.join(cur_lines).strip()))
    # 去掉空片（但保留有标题的空片）
    return [(t, c) for t, c in sections if c or t]


def _merge_small_sections(sections, min_chunk):
    """短章节并入上一节；第 0 片（引言）不会被吞"""
    merged = []
    for title, content in sections:
        if merged and content and len(content) < min_chunk:
            pt, pc = merged.pop()
            merged.append((pt, (pc + '\n\n' + content).strip()))
        else:
            merged.append((title, content))
    return merged


def _split_oversize(content, threshold):
    """超阈值内容按行切分；切在代码块内则闭合当前围栏并在下一片重开。
    断点在"加行之前"判定，所以每片 ≤ threshold；
    例外：单行本身超阈值（无法再切）→ 原样保留，由调用方记账告警。"""
    if len(content) <= threshold:
        return [content]
    parts = []
    buf, buf_len = [], 0
    fence_marker, fence_line = None, None

    def flush():
        nonlocal buf, buf_len
        if buf:
            parts.append('\n'.join(buf).strip('\n'))
        buf, buf_len = [], 0

    for ln in content.split('\n'):
        add = len(ln) + 1
        in_fence = fence_marker is not None
        closing = in_fence and _fence_close(ln, fence_marker)
        if buf and buf_len + add > threshold and not closing:
            if in_fence:
                buf.append(fence_marker)        # 先闭合当前围栏
                flush()
                if fence_line:
                    buf = [fence_line]          # 下一片重开同语言围栏
                    buf_len = len(fence_line) + 1
            else:
                flush()

        buf.append(ln)
        buf_len += add

        if fence_marker is None:
            m = _fence_open(ln)
            if m:
                fence_marker, fence_line = m, ln
        elif closing:
            fence_marker, fence_line = None, None

    flush()
    return [p for p in parts if p] or [content]


def write_markdown_and_images(page_id, markdown_slice, img_list, tag_str, skip_markdown=False):
    """写 markdown + 图片。
    返回 (ok: bool, detail: str, imgs_ok: int, imgs_total: int)
    ok=False 代表 markdown 没完整写入（失败或被截断）；图片缺失不算，记 detail。"""
    flags = []
    base64_total = sum(1 for src, _, _ in img_list if src and src.startswith('data:image/'))
    if not skip_markdown:
        if not markdown_slice:
            return True, 'ok(空)', 0, base64_total
        if len(markdown_slice) > MAX_CONTENT_CHARS:
            # 安全网：拆分器保证正常不会走到这；真走到了也不静默砍，记 flag 让上层判 PARTIAL
            orig = len(markdown_slice)
            markdown_slice = markdown_slice[:MAX_CONTENT_CHARS]
            flags.append(f'truncated:{orig}->{MAX_CONTENT_CHARS}')
            print(f"        ⚠ 单片超长被截断（{orig} → {MAX_CONTENT_CHARS} 字）")
        status = update_page_markdown(page_id, markdown_slice)
        if status not in (200, 201):
            return False, f'markdown_failed:{status}', 0, base64_total
    if base64_total == 0:
        detail = '; '.join(['ok'] + flags) if flags else 'ok'
        return (not flags), detail, 0, 0
    upload_map, up_fail = notion_upload_images(img_list)
    attached, at_fail = notion_attach_images(page_id, upload_map, markdown_slice)
    detail = f'imgs={attached}/{base64_total}'
    n_fail = len(up_fail) + len(at_fail)
    if n_fail:
        detail += f' fail={n_fail}'
    if flags:
        detail += '; ' + '; '.join(flags)
    return (not flags), detail, attached, base64_total


def repair_pages(records, entries):
    """--resume-write / --only-images：往已存在的页补写正文/图片。
    不建页、不去重、不推进度。records 来自 prev_results.json。"""
    by_id = {str(r.get('doc_id')): r for r in records}
    missing = [e for e in entries if str(e.get('doc_id')) not in by_id]
    batch = [e for e in entries if str(e.get('doc_id')) in by_id]
    if missing:
        print(f'[repair] 条目文件里有 {len(missing)} 篇在结果里没有对应记录（可能换过书），跳过')
    if not batch:
        print('ALL_DONE (nothing to repair)')
        return 0
    print(f'REPAIR: {len(batch)} 篇 | only_images={S.only_images}')
    ok = partial = failed = 0
    for e in batch:
        doc_id = str(e['doc_id'])
        rec = by_id[doc_id]
        print(f"  [{rec.get('idx','?')}] 补写 {str(e.get('title',''))[:50]}")
        result, fr = fetch_doc_with_retry(e, YUQUE_BOOK_ID)
        if result is None:
            print(f"    FAIL 抓取失败 {fr}")
            failed += 1
            continue
        markdown, img_list = lake_to_markdown(result.get('body', ''))
        chunks = split_markdown_by_headings(markdown)
        page_ids = rec.get('page_ids') or {}
        chunks_ok = 0
        imgs_ok_sum = 0
        imgs_total = 0
        notes = []
        for ci, (ct, c) in enumerate(chunks):
            pid = page_ids.get(str(ci)) or page_ids.get(ci)
            if not pid:
                notes.append(f'chunk{ci}:无page_id')
                continue
            okw, detail, io, it = write_markdown_and_images(
                pid, c, img_list, tag_str='', skip_markdown=S.only_images)
            imgs_ok_sum += io
            imgs_total = max(imgs_total, it)
            if okw:
                chunks_ok += 1
            else:
                notes.append(f'chunk{ci}:{detail}')
        good = chunks_ok == len(chunks) and not notes
        if S.only_images:
            good = imgs_ok_sum >= imgs_total or not notes
        status = 'OK' if good else 'PARTIAL'
        if status == 'OK':
            ok += 1
        else:
            partial += 1
        entry_result = {'idx': rec.get('idx'), 'status': status, 'title': e.get('title'),
                        'doc_id': doc_id, 'words': len(markdown),
                        'pages': len(page_ids) or 1,
                        'page_ids': page_ids,
                        'chunks_ok': chunks_ok, 'chunks_total': len(chunks),
                        'imgs_ok': imgs_ok_sum, 'imgs_total': imgs_total,
                        'detail': '; '.join(notes) if notes else ''}
        save_result(entry_result)
        print(f"    {status} | chunks={chunks_ok}/{len(chunks)} imgs={imgs_ok_sum}/{imgs_total} {'| '+'; '.join(notes) if notes else ''}")
        time.sleep(5)
    print(f'\nDONE: resume-write | OK={ok} PARTIAL={partial} FAIL={failed}')
    return 0


def extract_url(body, source):
    decoded = urllib.parse.unquote(body) if body else ""
    urls = re.findall(URL_PATTERN, decoded)
    if source == 'LINUX DO':
        for u in urls:
            if 'linux.do/t/' in u: return u.split('#')[0].split('?')[0]
    elif source == '博客园':
        for u in urls:
            if 'cnblogs.com/' in u and '/p/' in u: return u.split('#')[0].split('?')[0]
    elif source == 'B站':
        for u in urls:
            if 'bilibili.com/video/' in u or 'b23.tv/' in u: return u.split('#')[0].split('?')[0]
    elif source == 'Bangumi':
        for u in urls:
            if 'bgm.tv/' in u or 'bangumi.tv/' in u: return u.split('#')[0].split('?')[0]
    elif source == '小黑盒':
        for u in urls:
            if 'xiaoheihe.cn/' in u: return u.split('#')[0].split('?')[0]
    return None

def extract_tags(title, body, source, category):
    keywords = []
    decoded = urllib.parse.unquote(body) if body else ""
    text = re.sub(r'<[^>]+>', ' ', decoded)
    text = re.sub(r'\s+', ' ', text).strip()

    ctitle = title
    for term in ['MyBatis-Plus','Mybatis-Plus','mybatis-plus','Spring-Boot','Spring-Cloud',
                 'Cloud-Alibaba','CloudAlibaba','Element-UI','ElementUI']:
        ctitle = ctitle.replace(term, term.replace('-', ''))
    title_clean = re.sub(r'[+_【】\[\]｜|()（）\-—，。！？、:：""\'"\/]', ' ', ctitle)
    for w in title_clean.split():
        w = w.strip('+-./ ').strip()
        if len(w) >= 2 and len(w) <= 20:
            keywords.append(w)

    src_tags = {'博客园': ['博客园'], 'LINUX DO': ['LINUX DO'], 'Bangumi': ['Bangumi'], 'B站': ['B站'], '小黑盒': ['小黑盒']}
    if source in src_tags: keywords.extend(src_tags[source])

    cat_tags = {
        'AI/大模型': ['AI','大模型'], '编程技术': ['编程','开发'], '操作系统': ['系统','操作系统'],
        '安全': ['安全'], '影视/动漫': ['影视','动漫'], '日记/随笔': ['日记','随笔'],
        '工作/求职': ['工作','求职'], '投资/财经': ['投资','财经'], '读书': ['读书'],
        '游戏': ['游戏'], '音乐制作': ['音乐'], '福利/羊毛': ['福利','羊毛']
    }
    if category in cat_tags: keywords.extend(cat_tags[category])

    tech = re.findall(
        r'(Python|Java|JavaScript|TypeScript|Go|Rust|SQL|Docker|Kubernetes|K8s|'
        r'Git|React|Vue|Node\.?js|Linux|Windows|Ubuntu|CentOS|'
        r'Claude|ChatGPT|GPT|Gemini|DeepSeek|Kimi|Grok|OpenAI|Sora|LLM|'
        r'AI|API|CSS|HTML|Redis|MySQL|nginx|Android|iOS|Flutter|VSCode|'
        r'Django|Spring|Flask|Maven|Gradle|Webpack|Vite|Jest|'
        r'Mybatis|RabbitMQ|RocketMQ|Kafka|Zookeeper|Dubbo|Nacos|Apollo|Seata|'
        r'Elasticsearch|Logstash|Kibana|Prometheus|Grafana|Jenkins|'
        r'PostgreSQL|MongoDB|MariaDB|Oracle|SQLite|Hadoop|Spark|Flink|Hive)',
        text, re.IGNORECASE)
    keywords.extend(tech)

    seen = set()
    result = []
    for k in keywords:
        k = k.strip()
        if re.match(r'^\d+$', k):
            continue
        if k and k not in seen and len(k) >= 2:
            seen.add(k)
            result.append(k)
    return result[:15]


# ── 主流程 ──

# ── 主流程 ──
# MODE 已由 cli 在开头解析（支持 --mode，也兼容旧的裸 `new|resume|retry-failed`）

if MODE == 'new':
    for f in [PROGRESS_FILE, PREV_RESULTS_FILE]:
        if os.path.exists(f):
            os.remove(f)
            print(f"[mode=new] 已清除 {f}")
    print('[mode=new] 从头开始')

# ── 启动期探活：目标库 / 判重字段不对就别跑（fail-fast，不写入、不推进进度）──
try:
    resolve_data_source_id()
except NotionInitError as e:
    print(f'[FATAL] Notion 探活失败: {e}')
    print('[FATAL] 未做任何写入，未推进进度。修好 config 再跑。')
    sys.exit(2)

# ───── 载入条目（进度校验/指纹都要它）─────
if not os.path.exists(DATA_FILE):
    print(f'[FATAL] 条目文件不存在: {DATA_FILE}')
    print('[FATAL] 先跑 build_entries.py 生成（可用 --book-id 指定知识库）。')
    sys.exit(2)
with open(DATA_FILE) as f:
    entries = json.load(f)

ENTRY_FP = st.entries_fingerprint(entries)
JOB_KEY = st.job_key(S.book_id, S.notion_db_id)

# ── 补写模式：只往已存在的页补，不建页/不去重/不推进度 ──
if S.resume_write:
    all_recs = st.load_results(PREV_RESULTS_FILE)
    if not all_recs:
        print(f'[FATAL] 没有结果可补：{PREV_RESULTS_FILE}')
        sys.exit(2)
    if S.only_images:
        records = [r for r in all_recs if r.get('imgs_total', 0) > r.get('imgs_ok', 0)]
    else:
        records = [r for r in all_recs if r.get('status') == 'PARTIAL']
    if S.dry_run:
        print(f'[dry-run] 补写模式：待处理 {len(records)} 篇（only_images={S.only_images}）')
        for r in records:
            print(f"   [{r.get('idx','?')}] {r.get('title','')[:60]} imgs={r.get('imgs_ok')}/{r.get('imgs_total')}")
        print('[dry-run] 结束，未写入任何内容')
        sys.exit(0)
    sys.exit(repair_pages(records, entries))

# ───── 任务进度（按 book:db 分桶，带清单指纹）─────
progress = st.load_progress(PROGRESS_JSON)
job = st.get_job(progress, JOB_KEY)
legacy_cursor = 0
# 只在"从没写过 progress.json"时迁旧文件（且只做一次）。否则新任务会继承别的书的游标
if job is None and not os.path.exists(PROGRESS_JSON) and os.path.exists(PROGRESS_FILE):
    try:
        legacy_cursor = int((open(PROGRESS_FILE).read() or '0').strip() or 0)
        print(f'[progress] 迁移旧 progress.txt → progress.json（游标 {legacy_cursor}，归属未校验）')
    except Exception:
        legacy_cursor = 0
if job is None:
    st.set_job(progress, JOB_KEY, st.new_job(S.book_id, S.notion_db_id, len(entries), ENTRY_FP,
                                             cursor=legacy_cursor))
    st.save_progress(PROGRESS_JSON, progress)
    print(f'[progress] 新建任务 {JOB_KEY}（{len(entries)} 篇）')
else:
    fp_changed = bool(job.get('entries_fingerprint')) and job['entries_fingerprint'] != ENTRY_FP
    if fp_changed and not S.reset_cursor:
        print('[FATAL] 条目清单变了，与进度记录对不上：')
        print(f"[FATAL]   进度内: {job['entries_fingerprint']}（{job.get('entries_count')} 篇）")
        print(f"[FATAL]   当前:   {ENTRY_FP}（{len(entries)} 篇）")
        print('[FATAL] 清单顺序变了却沿用旧游标会静默漏篇。确认要重扫这份清单就加 --reset-cursor')
        sys.exit(2)
    if fp_changed:
        print('[progress] --reset-cursor：清单指纹已变更，游标归零重扫')
    elif S.reset_cursor:
        print('[progress] --reset-cursor：游标归零')
    if S.reset_cursor or fp_changed:
        st.update_cursor(progress, JOB_KEY, 0, allow_decrease=True)
        job['entries_fingerprint'] = ENTRY_FP
        job['entries_count'] = len(entries)
        st.save_progress(PROGRESS_JSON, progress)

start_idx = int(st.get_job(progress, JOB_KEY).get('cursor', 0))

# 已处理的 doc_id：OK/PARTIAL 都算（PARTIAL 另有一条 --resume-write 补写通道）
merged_records = st.load_results(PREV_RESULTS_FILE)
processed_ids = {str(r.get('doc_id')) for r in merged_records if r.get('status') in ('OK', 'PARTIAL')}

# ───── 构建批次 ─────
batch_start_cursor = start_idx
if MODE == 'new':
    print('[mode=new] 重置进度 / 结果 / 重试队列')
    st.update_cursor(progress, JOB_KEY, 0, allow_decrease=True)
    st.save_progress(PROGRESS_JSON, progress)
    st.save_results(PREV_RESULTS_FILE, [])
    st.save_queue(RETRY_QUEUE_FILE, [])
    processed_ids = set()
    start_idx = batch_start_cursor = 0
    end_idx = min(BATCH_SIZE, len(entries))
    batch = entries[:end_idx]
    if not batch:
        print('ALL_DONE')
        sys.exit(0)
    print(f'BATCH: 0-{end_idx-1}/{len(entries)}')
elif MODE == 'retry-failed':
    q = st.load_queue(RETRY_QUEUE_FILE)
    if not q:
        q = [str(r.get('doc_id')) for r in merged_records
             if r.get('status') in ('FAIL', 'RATE_LIMITED')]
        st.save_queue(RETRY_QUEUE_FILE, q)
        print(f'[mode=retry-failed] 队列为空，从结果文件重建 {len(q)} 篇')
    by_id = {str(e['doc_id']): e for e in entries}
    batch = [by_id[d] for d in q[:BATCH_SIZE] if d in by_id]
    if not batch:
        print('ALL_DONE (no failed docs to retry)')
        sys.exit(0)
    print(f'RETRY-FAILED BATCH: {len(batch)}（队列共 {len(q)}）')
else:
    end_idx = min(start_idx + BATCH_SIZE, len(entries))
    batch = entries[start_idx:end_idx]
    if not batch:
        print('ALL_DONE')
        sys.exit(0)
    qn = len(st.load_queue(RETRY_QUEUE_FILE))
    print(f'BATCH: {start_idx}-{end_idx-1}/{len(entries)}' + (f' | 重试队列待处理 {qn} 篇（跑 --mode retry-failed 清）' if qn else ''))

print()

if S.dry_run:
    j = st.get_job(progress, JOB_KEY) or {}
    print('[dry-run] 仅探活 + 列批次，不写任何东西')
    print(f"[dry-run] 任务={JOB_KEY} 游标={j.get('cursor')} 清单指纹={j.get('entries_fingerprint')}")
    print(f'[dry-run] 模式={MODE} 本次待处理 {len(batch)} 篇')
    for k, e in enumerate(batch):
        print(f'   [{start_idx + k}] {e["doc_id"]}  {str(e.get("title", ""))[:60]}')
    print('[dry-run] 结束，未写入任何内容')
    sys.exit(0)

results = []
dedup_fail_streak = 0  # 去重查询连续失败计数（fail-fast）
for i, entry in enumerate(batch):
    idx = start_idx + i
    doc_id = str(entry['doc_id'])

    # ───── 去重：本地已记录 ─────
    if doc_id in processed_ids:
        print(f"  [{idx}] SKIP（已迁移）- {entry['title'][:50]}")
        entry_result = {'idx': idx, 'status': 'SKIP', 'title': entry['title'][:50], 'doc_id': doc_id}
        results.append(entry_result)
        save_result(entry_result, idx)
        continue

    # ───── 取文档 ─────
    result, fail_reason = fetch_doc_with_retry(entry, YUQUE_BOOK_ID)
    if result is None:
        status = fail_reason or 'FAIL'
        print(f"  [{idx}] {status}: {entry['title'][:50]}")
        entry_result = {'idx': idx, 'status': status, 'title': entry['title'][:50], 'doc_id': doc_id}
        results.append(entry_result)
        save_result(entry_result, idx)
        continue

    body = result.get('body', '')

    # ───── 文档预检 ─────
    precheck_pass, precheck_reason = doc_body_precheck(result, entry)
    if not precheck_pass:
        print(f"  [{idx}] SKIP ({precheck_reason}) - {entry['title'][:50]}")
        entry_result = {'idx': idx, 'status': 'SKIP', 'title': entry['title'][:50],
                        'doc_id': doc_id, 'reason': precheck_reason}
        results.append(entry_result)
        save_result(entry_result, idx)
        continue

    # ───── lake → markdown ─────
    markdown, img_list = lake_to_markdown(body)
    unsupported = count_unsupported_cards(markdown)
    url = extract_url(body, entry['source'])
    tags = extract_tags(entry['title'], body, entry['source'], entry['category'])

    yuque_url = f'https://www.yuque.com/{YUQUE_USER}/{YUQUE_NAMESPACE}/{result.get("slug", "")}'
    tag_str = '/'.join(tags[:6])
    if len(tags) > 6: tag_str += f"...(+{len(tags)-6})"

    # ───── 构建 Notion 属性 ─────
    props = {
        '名称': {'title': [{'text': {'content': entry['title'][:2000]}}]},
        '来源': {'select': {'name': entry['source']}},
        '分类': {'select': {'name': entry['category']}},
        '字数': {'number': result.get('word_count', 0)},
        '标签': {'multi_select': [{'name': t} for t in tags[:15]]},
        '原文链接': {'url': yuque_url}
    }
    if url:
        props['备注'] = {'rich_text': [{'text': {'content': f'原文出处: {url}'}}]}

    # ───── 去重：Notion 侧（fail-fast，查询失败不当“不存在”）─────
    try:
        notion_exists = check_notion_exists(yuque_url)
    except NotionInitError as e:
        dedup_fail_streak += 1
        print(f"  [{idx}] FAIL: 去重查询异常 - {e}")
        entry_result = {'idx': idx, 'status': 'FAIL', 'title': entry['title'][:50],
                        'doc_id': doc_id, 'reason': 'dedup_query_failed'}
        results.append(entry_result)
        save_result(entry_result, idx)
        if dedup_fail_streak >= DEDUP_MAX_CONSECUTIVE_FAILURES:
            print(f"[FATAL] 去重查询连续失败 {dedup_fail_streak} 篇，整跑中止（防批量重复建页）。")
            print(f"[FATAL] 已处理结果已落盘 {PREV_RESULTS_FILE}，进度文件未推进。")
            sys.exit(2)
        continue
    dedup_fail_streak = 0

    if notion_exists:
        print(f"  [{idx}] SKIP（Notion 已存在）- {entry['title'][:50]}")
        entry_result = {'idx': idx, 'status': 'SKIP', 'title': entry['title'][:50],
                        'doc_id': doc_id, 'reason': 'notion_exists'}
        results.append(entry_result)
        save_result(entry_result, idx)
        continue

    # ───── 拆分 Big Doc ─────
    chunks = split_markdown_by_headings(markdown)

    # 创建主页面
    page = create_notion_page(props)
    if not page or not page.get('id'):
        print(f"  [{idx}] FAIL: 创建页失败 - {entry['title'][:50]}")
        entry_result = {'idx': idx, 'status': 'FAIL', 'title': entry['title'][:50],
                        'doc_id': doc_id, 'reason': 'create_page_failed'}
        results.append(entry_result)
        save_result(entry_result, idx)
        continue
    main_page_id = page['id']

    page_ids = {'0': main_page_id}  # chunk 下标 -> page_id
    pages_created = 1
    chunks_ok = 0
    chunks_total = len(chunks)
    imgs_ok_total = 0
    imgs_total_total = 0
    problems = []
    split_notes = []

    for ci, (chapter_title, chapter_content) in enumerate(chunks):
        if ci == 0:
            target_page = main_page_id
        else:
            # 第 N 章 → 创建独立页面
            child_title = f"{entry['title'][:60]} - {chapter_title}"
            child_props = {
                '名称': {'title': [{'text': {'content': child_title[:2000]}}]},
                '来源': {'select': {'name': entry['source']}},
                '分类': {'select': {'name': entry['category']}},
                '字数': {'number': result.get('word_count', 0)},
                '标签': {'multi_select': [{'name': t} for t in tags[:15]]},
                '原文链接': {'url': yuque_url}
            }
            # 备注：父文档来源
            child_note = f"从语雀「{YUQUE_NAMESPACE}」中的「{entry['title']}」拆分而来，此为第{ci+1}章「{chapter_title}」"
            child_props['备注'] = {'rich_text': [{'text': {'content': child_note[:2000]}}]}

            child_page = create_notion_page(child_props)
            if not child_page or not child_page.get('id'):
                problems.append(f'chunk{ci}:子页创建失败:{chapter_title[:20]}')
                split_notes.append(f"❌{chapter_title[:40]}(创建失败)")
                continue
            target_page = child_page['id']
            page_ids[str(ci)] = target_page
            pages_created += 1
            split_notes.append(chapter_title[:50])
            time.sleep(5)  # 子页面间稍等防止限流

        okw, detail, io, it = write_markdown_and_images(target_page, chapter_content, img_list, tag_str)
        imgs_ok_total += io
        imgs_total_total = max(imgs_total_total, it)
        if okw:
            chunks_ok += 1
        else:
            problems.append(f'chunk{ci}:{detail}')
            split_notes.append(f"❌{chapter_title[:40]}(正文写失败)")

    # ───── 状态判定 ─────
    status = 'OK' if (chunks_ok == chunks_total and not problems) else 'PARTIAL'
    detail = '; '.join(problems) if problems else ''

    # ───── 打印结果 ─────
    print(f"  [{idx}] {status} | {entry['category']:8s} | {len(markdown):5d}字 | chunks={chunks_ok}/{chunks_total} | imgs={imgs_ok_total}/{imgs_total_total} | TAGS: {tag_str}")
    if is_split := pages_created > 1:
        for sn in split_notes:
            print(f"        ↳ {sn}")
    if detail:
        print(f"        ⚠ {detail}")

    print(f"        原文链接: {yuque_url}")
    if unsupported:
        print(f"        ⚠ 未转换卡片: " + ', '.join(f'{k}×{v}' for k, v in unsupported.items()))
    if url:
        print(f"        备注: 原文出处: {url}")

    entry_result = {'idx': idx, 'status': status, 'title': entry['title'][:50], 'doc_id': doc_id,
                    'words': len(markdown), 'tags': len(tags), 'pages': pages_created,
                    'page_ids': page_ids,
                    'chunks_ok': chunks_ok, 'chunks_total': chunks_total,
                    'imgs_ok': imgs_ok_total, 'imgs_total': imgs_total_total,
                    'unsupported_cards': unsupported,
                    'detail': detail}
    results.append(entry_result)
    save_result(entry_result, idx)
    time.sleep(20)

# ───── 保存进度 / 批次收尾 ─────
ok = sum(1 for r in results if r['status'] == 'OK')
partial = sum(1 for r in results if r['status'] == 'PARTIAL')
skipped = sum(1 for r in results if r['status'] == 'SKIP')
failed = sum(1 for r in results if r['status'] in ('FAIL', 'RATE_LIMITED'))
productive = ok + partial + skipped
last_batch = {'ok': ok, 'partial': partial, 'skip': skipped, 'fail': failed}

if MODE == 'retry-failed':
    qn = len(st.load_queue(RETRY_QUEUE_FILE))
    print(f"\nDONE: retry-failed | OK={ok} PARTIAL={partial} FAIL={failed} SKIP={skipped} | 队列剩 {qn}")
    if qn:
        print(f"NEXT: 继续跑 --mode retry-failed（还剩 {qn} 篇）")
    else:
        print("ALL_DONE (重试队列已清空)")
else:
    cursor_after = int(st.get_job(progress, JOB_KEY).get('cursor', 0))

    # 安全阀：整批全废（无 OK/PARTIAL/SKIP）→ 回滚游标到批首，停跑
    if len(batch) > 0 and productive == 0:
        st.update_cursor(progress, JOB_KEY, batch_start_cursor, allow_decrease=True,
                         last_batch=last_batch)
        st.save_progress(PROGRESS_JSON, progress)
        print(f"\n[FATAL] 本批 {len(batch)} 篇全部失败（无 OK/PARTIAL/SKIP）→ 疑似语雀风控或网络故障。")
        print(f"[FATAL] 游标已回滚到批首 {batch_start_cursor}，未推进。稍后重跑即可续上。")
        sys.exit(2)

    st.update_cursor(progress, JOB_KEY, cursor_after, last_batch=last_batch)
    st.save_progress(PROGRESS_JSON, progress)
    with open(PROGRESS_FILE, 'w') as f:   # 老格式镜像，方便人看/兼容
        f.write(str(cursor_after))

    print(f"\nDONE: start={start_idx} | OK={ok} PARTIAL={partial} FAIL={failed} SKIP={skipped} | 游标: {cursor_after}/{len(entries)}")
    if failed:
        print(f"HINT: {failed} 篇失败已进重试队列，跑 --mode retry-failed 补")
    if partial:
        print(f"HINT: {partial} 篇不完整，跑 --resume-write 补写（--only-images 只补图）")
    if cursor_after >= len(entries):
        print("ALL_DONE")
    else:
        print(f"NEXT: {cursor_after}/{len(entries)}")