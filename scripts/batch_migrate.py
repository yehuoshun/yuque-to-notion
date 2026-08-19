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

URL_PATTERN = r'https?://[^\s"<>\')\]]+'
DATA_FILE = '/tmp/yuque_entries.json'
PROGRESS_FILE = '/tmp/batch_progress.txt'

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

# body_to_markdown 已由 lake_convert.lake_to_markdown 替代

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
    result = mcpcall('yuque-mcp.yuque_web_get_doc', {
        'id': str(entry['doc_id']), 'book_id': YUQUE_BOOK_ID
    })
    # web 接口限流时返回空 body：不重试轰炸，失败即停（避免把风控窗口填满）
    if result is None:
        print(f"  [{idx}] FAIL: 读档失败 - {entry['title'][:50]}")
        results.append({'idx': idx, 'status': 'FAIL', 'title': entry['title'][:50]})
        continue
    if not result.get('body'):
        print(f"  !! 风控触发（body 空）: 批次中止，静默恢复后重跑。进度保持 {start_idx}")
        results.append({'idx': idx, 'status': 'RATE_LIMITED', 'title': entry['title'][:50]})
        break
    
    body = result.get('body', '')
    markdown = lake_to_markdown(body)
    url = extract_url(body, entry['source'])
    tags = extract_tags(entry['title'], body, entry['source'], entry['category'])
    
    yuque_url = f'https://www.yuque.com/{YUQUE_USER}/{YUQUE_NAMESPACE}/{result.get("slug", "")}'
    
    props = {
        '名称': {'title': [{'text': {'content': entry['title'][:2000]}}]},
        '来源': {'select': {'name': entry['source']}},
        '分类': {'select': {'name': entry['category']}},
        '字数': {'number': entry['word_count']},
        '标签': {'multi_select': [{'name': t} for t in tags[:15]]},
        '原文链接': {'url': yuque_url}
    }
    if url:
        props['备注'] = {'rich_text': [{'text': {'content': f'原文出处: {url}'}}]}
    
    page = create_notion_page(props)
    
    if not page or not page.get('id'):
        print(f"  [{idx}] FAIL: 创建页失败 - {entry['title'][:50]}")
        results.append({'idx': idx, 'status': 'FAIL', 'title': entry['title'][:50]})
        continue
    
    if markdown:
        if len(markdown) > 50000:
            markdown = markdown[:50000] + "\n\n...（内容过长已截断）"
        update_page_markdown(page['id'], markdown)
    
    tag_str = '/'.join(tags[:6])
    if len(tags) > 6: tag_str += f"...(+{len(tags)-6})"
    print(f"  [{idx}] OK | {entry['category']:8s} | {len(markdown):5d}字 | TAGS: {tag_str}")
    print(f"        原文链接: https://www.yuque.com/yehuoshun/twpgac/{result.get('slug', '')}")
    if url:
        print(f"        备注: 原文出处: {url}")
    results.append({'idx': idx, 'status': 'OK', 'title': entry['title'][:50], 'words': len(markdown), 'tags': len(tags)})
    time.sleep(20)

# Save progress
with open(PROGRESS_FILE, 'w') as f:
    f.write(str(end_idx))

ok = sum(1 for r in results if r['status'] == 'OK')
fail = sum(1 for r in results if r['status'] == 'FAIL')
print(f"\nDONE: {start_idx}-{end_idx-1} | OK={ok} FAIL={fail}")
print(f"NEXT: {end_idx}" if end_idx < len(entries) else "ALL_DONE")