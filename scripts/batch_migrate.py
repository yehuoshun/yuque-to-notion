import subprocess, json, urllib.parse, re, time, sys, os
import requests
from config import *
from lake_convert import lake_to_markdown

# Notion 直接 REST 调用（绕开 mcporter 命令行 ARG_MAX 限制）
with open('/home/admin/.openclaw/secrets/credentials.env') as _f:
    _env = _f.read()
_m = re.search(r'NOTION_TOKEN[=:]\s*[\"\x27]?([^\"\x27\n]+)', _env)
NOTION_TOKEN = _m.group(1).strip().strip('"').strip("'") if _m else ''
NOTION_HEADERS = {
    'Authorization': f'Bearer {NOTION_TOKEN}',
    'Notion-Version': '2026-03-11',
    'Content-Type': 'application/json'
}

def create_notion_page(props):
    r = requests.post('https://api.notion.com/v1/pages',
        headers=NOTION_HEADERS,
        json={'parent': {'database_id': NOTION_DATABASE_ID}, 'properties': props}, timeout=60)
    if r.status_code in (200, 201):
        return r.json()
    print(f"    [notion] 创建失败 {r.status_code}: {r.text[:200]}")
    return None

def update_page_markdown(page_id, markdown):
    r = requests.patch(f'https://api.notion.com/v1/pages/{page_id}/markdown',
        headers=NOTION_HEADERS,
        json={'type': 'replace_content', 'replace_content': {'new_str': markdown, 'allow_deleting_content': True}},
        timeout=120)
    if r.status_code not in (200, 201):
        print(f"    [notion] markdown 写入失败 {r.status_code}: {r.text[:200]}")
        return False
    return True

import base64

def notion_upload_images(image_srcs):
    """原生上传 base64 图片到 Notion，返回 {placeholder_url: file_upload_id}"""
    result = {}
    for i, (src, link, ext) in enumerate(image_srcs):
        if not src or not src.startswith('data:image/'):
            continue
        b64 = src.split(',', 1)[1] if ',' in src else src
        try:
            raw = base64.b64decode(b64)
        except Exception:
            continue
        if not raw:
            continue
        r1 = requests.post('https://api.notion.com/v1/file_uploads',
            headers=NOTION_HEADERS,
            json={'mode': 'single_part', 'filename': f'img_{i}.{ext}', 'content_type': f'image/{ext}'},
            timeout=60)
        if r1.status_code not in (200, 201):
            print(f"    [img] 创建上传失败 {r1.status_code}: {r1.text[:150]}")
            continue
        fu = r1.json()
        r2 = requests.post(fu['upload_url'],
            headers={'Authorization': f'Bearer {NOTION_TOKEN}', 'Notion-Version': '2026-03-11'},
            files={'file': (f'img_{i}.{ext}', raw, f'image/{ext}')}, timeout=120)
        if r2.status_code not in (200, 201):
            print(f"    [img] 发送内容失败 {r2.status_code}: {r2.text[:150]}")
            continue
        placeholder = f'https://n2m.local/img_{i}.{ext}'
        result[placeholder] = fu['id']
    return result

def notion_attach_images(page_id, upload_map, markdown):
    """读回页面 blocks，把占位图 PATCH 成 file_upload 类型"""
    r = requests.get(f'https://api.notion.com/v1/blocks/{page_id}/children',
        headers=NOTION_HEADERS, timeout=60)
    if r.status_code != 200:
        print(f"    [img] 读 blocks 失败 {r.status_code}")
        return 0
    attached = 0
    for b in r.json().get('results', []):
        if b.get('type') != 'image':
            continue
        ext = (b.get('image') or {}).get('external') or {}
        url = ext.get('url', '')
        if url not in upload_map:
            continue
        fuid = upload_map[url]
        r2 = requests.patch(f"https://api.notion.com/v1/blocks/{b['id']}", headers=NOTION_HEADERS,
            json={'image': {'file_upload': {'id': fuid}}}, timeout=60)
        if r2.status_code in (200, 201):
            attached += 1
        else:
            print(f"    [img] PATCH 失败 {r2.status_code}: {r2.text[:150]}")
    return attached

URL_PATTERN = r'https?://[^\s"<>\')\]]+'
DATA_FILE = '/tmp/yuque_entries.json'
PROGRESS_FILE = '/tmp/yuque_batch_progress.txt'
PREV_RESULTS_FILE = '/tmp/yuque_prev_results.json'

def mcpcall(tool, args, retries=3):
    cmd = ['mcporter', 'call', tool, '--args', json.dumps(args), '--output', 'json']
    # 必须固定 cwd：mcporter 按 cwd 找 MCP server 配置，/tmp 下找不到 yuque-mcp
    MCP_CWD = '/home/admin/.openclaw/workspace'
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
        # None = 网络/超时/系统错误
        if result is None:
            if attempt < max_retries:
                print(f"    [retry] 网络异常, 等 10s 后重试 ({attempt+1}/{max_retries})")
                time.sleep(10)
            continue
        # 正常结果：有 body
        if result.get('body'):
            return result, None
        # body 空 = 风控限流：等更久再重试
        if attempt == 0:
            print(f"    [retry] body 空（疑似风控）, 等 30s 后重试")
            time.sleep(30)
        else:
            print(f"    [retry] body 仍空, 等 60s 后重试 ({attempt+1}/{max_retries})")
            time.sleep(60)
    return None, 'RATE_LIMITED'

def save_result(result_json):
    """每篇处理完立即写盘，进程中断不丢结果"""
    tmp_path = PREV_RESULTS_FILE + '.tmp'
    try:
        existing = []
        if os.path.exists(PREV_RESULTS_FILE):
            with open(PREV_RESULTS_FILE) as f:
                existing = json.load(f)
        existing.append(result_json)
        with open(tmp_path, 'w') as f:
            json.dump(existing, f, ensure_ascii=False, indent=1)
        os.replace(tmp_path, PREV_RESULTS_FILE)
    except Exception as e:
        print(f"    [save] 写盘失败: {e}")

def doc_body_precheck(result, entry):
    """文档预检：R1 二进制 / R2 数据库dump / R8 无意义内容。返回 (pass: bool, reason: str|None)"""
    body = result.get('body', '')
    word_count = result.get('word_count', 0)
    if not body:
        return False, 'R8_空body'

    total = len(body)
    # R1: 二进制 — 可读文本比例 < 5%，且无 lake card 标签
    readable = len(re.findall(r'[\u4e00-\u9fff\w]{2,}', body))
    has_card = '<card' in body
    if not has_card and readable < max(5, total * 0.05) and total > 500:
        return False, 'R1_二进制'

    # R2: 数据库 dump — 10+ 条 SQL 模式
    sql_lines = len(re.findall(r'(CREATE\s+TABLE|INSERT\s+INTO|DROP\s+TABLE|ALTER\s+TABLE|' +
                                r'CREATE\s+INDEX|TRUNCATE\s+TABLE)', body, re.I))
    if sql_lines > 10:
        return False, 'R2_数据库dump'

    # R8: 无意义内容 — 可读字符 < 10 且 word_count < 5，且无 card
    if not has_card and readable < 10 and word_count < 5:
        return False, 'R8_无意义内容'

    return True, None


def check_notion_exists(yuque_url):
    """查 Notion 数据库是否有 原文链接=yuque_url 的记录"""
    r = requests.post(
        f'https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query',
        headers=NOTION_HEADERS,
        json={
            'filter': {
                'property': '原文链接',
                'url': {'equals': yuque_url}
            }
        },
        timeout=30
    )
    if r.status_code != 200:
        return False  # 查失败不拦截，宁可重复不可漏
    return len(r.json().get('results', [])) > 0


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

    # Clean title: remove all special chars, split into words
    # Handle compound terms (e.g. MyBatis-Plus -> MybatisPlus)
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
        # Skip purely numeric tags
        if re.match(r'^\d+$', k):
            continue
        if k and k not in seen and len(k) >= 2:
            seen.add(k)
            result.append(k)
    return result[:15]

# Read progress
start_idx = 0
if os.path.exists(PROGRESS_FILE):
    with open(PROGRESS_FILE) as f:
        start_idx = int(f.read().strip())

# Load already-processed doc_ids for skip-on-exist
processed_ids = set()
if os.path.exists(PREV_RESULTS_FILE):
    try:
        with open(PREV_RESULTS_FILE) as f:
            for r in json.load(f):
                if r.get('status') == 'OK':
                    processed_ids.add(r.get('doc_id', ''))
    except Exception:
        pass

with open(DATA_FILE) as f:
    entries = json.load(f)

end_idx = min(start_idx + BATCH_SIZE, len(entries))
batch = entries[start_idx:end_idx]

if not batch:
    print("ALL_DONE")
    sys.exit(0)

print(f"BATCH: {start_idx}-{end_idx-1}/{len(entries)}")
print()

results = []
for i, entry in enumerate(batch):
    idx = start_idx + i
    doc_id = str(entry['doc_id'])

    # 去重：已成功迁移的跳过
    if doc_id in processed_ids:
        print(f"  [{idx}] SKIP（已迁移）- {entry['title'][:50]}")
        results.append({'idx': idx, 'status': 'SKIP', 'title': entry['title'][:50], 'doc_id': doc_id})
        continue

    # 取文档（含渐进退避：网络异常→10s, body空→30s/60s, 最多重试2次）
    result, fail_reason = fetch_doc_with_retry(entry, YUQUE_BOOK_ID)
    if result is None:
        status = fail_reason or 'FAIL'
        print(f"  [{idx}] {status}: {entry['title'][:50]}")
        entry_result = {'idx': idx, 'status': status, 'title': entry['title'][:50], 'doc_id': doc_id}
        results.append(entry_result)
        save_result(entry_result)
        continue

    body = result.get('body', '')

    # 文档预检：R1 二进制 / R2 数据库dump / R8 无意义内容
    precheck_pass, precheck_reason = doc_body_precheck(result, entry)
    if not precheck_pass:
        print(f"  [{idx}] SKIP ({precheck_reason}) - {entry['title'][:50]}")
        entry_result = {'idx': idx, 'status': 'SKIP', 'title': entry['title'][:50],
                        'doc_id': doc_id, 'reason': precheck_reason}
        results.append(entry_result)
        save_result(entry_result)
        continue

    markdown, img_list = lake_to_markdown(body)
    url = extract_url(body, entry['source'])
    tags = extract_tags(entry['title'], body, entry['source'], entry['category'])

    yuque_url = f'https://www.yuque.com/{YUQUE_USER}/{YUQUE_NAMESPACE}/{result.get("slug", "")}'

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

    # 去重：查 Notion 是否已有同原文链接的记录
    if check_notion_exists(yuque_url):
        print(f"  [{idx}] SKIP（Notion 已存在）- {entry['title'][:50]}")
        entry_result = {'idx': idx, 'status': 'SKIP', 'title': entry['title'][:50],
                        'doc_id': doc_id, 'reason': 'notion_exists'}
        results.append(entry_result)
        save_result(entry_result)
        continue

    page = create_notion_page(props)

    if not page or not page.get('id'):
        print(f"  [{idx}] FAIL: 创建页失败 - {entry['title'][:50]}")
        entry_result = {'idx': idx, 'status': 'FAIL', 'title': entry['title'][:50],
                        'doc_id': doc_id, 'reason': 'create_page_failed'}
        results.append(entry_result)
        save_result(entry_result)
        continue

    if markdown:
        if len(markdown) > 50000:
            markdown = markdown[:50000] + "\n\n...（内容过长已截断）"
        update_page_markdown(page['id'], markdown)
        if img_list:
            upload_map = notion_upload_images(img_list)
            if upload_map:
                n = notion_attach_images(page['id'], upload_map, markdown)
                if n: print(f"        [img] 已原生上传 {n} 张")

    tag_str = '/'.join(tags[:6])
    if len(tags) > 6: tag_str += f"...(+{len(tags)-6})"
    print(f"  [{idx}] OK | {entry['category']:8s} | {len(markdown):5d}字 | TAGS: {tag_str}")
    print(f"        原文链接: https://www.yuque.com/yehuoshun/twpgac/{result.get('slug', '')}")
    if url:
        print(f"        备注: 原文出处: {url}")
    entry_result = {'idx': idx, 'status': 'OK', 'title': entry['title'][:50], 'doc_id': doc_id,
                    'words': len(markdown), 'tags': len(tags)}
    results.append(entry_result)
    save_result(entry_result)
    time.sleep(20)

# Save progress: 实际消耗了多少篇（skip已迁移的不算消耗）
# 避免风控跳过导致进度跳空丢文档
actually_consumed = sum(1 for r in results if r['status'] != 'SKIP')
next_start = start_idx + actually_consumed
with open(PROGRESS_FILE, 'w') as f:
    f.write(str(next_start))

ok = sum(1 for r in results if r['status'] == 'OK')
skipped = sum(1 for r in results if r['status'] == 'SKIP')
failed = sum(1 for r in results if r['status'] in ('FAIL', 'RATE_LIMITED'))
print(f"\nDONE: start={start_idx} | OK={ok} FAIL={failed} SKIP(已迁移)={skipped} | 下次: {next_start}")
if next_start >= len(entries):
    print("ALL_DONE")
else:
    print(f"NEXT: {next_start}/{len(entries)}")