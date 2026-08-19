import subprocess, json, urllib.parse, re, time, sys, os
from config import *

URL_PATTERN = r'https?://[^\s"<>\')\]]+'
DATA_FILE = '/tmp/yuque_entries.json'
PROGRESS_FILE = '/tmp/batch_progress.txt'

def mcpcall(tool, args):
    cmd = ['mcporter', 'call', tool, '--args', json.dumps(args), '--output', 'json']
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        data = json.loads(r.stdout)
        if isinstance(data, dict) and data.get('isError'): return None
        return data
    except: return None

def body_to_markdown(body):
    if not body: return ""
    decoded = urllib.parse.unquote(body)
    
    # Step 1: Replace card elements with markdown
    # Code blocks
    def replace_codeblock(m):
        raw_val = m.group(1)
        val = urllib.parse.unquote(raw_val)
        if val.startswith('data:'): val = val[5:]
        try:
            d = json.loads(val)
            code = d.get('code', '')
            mode = d.get('mode', 'text')
            if '%' in code: code = urllib.parse.unquote(code)
            lang = mode if mode != 'plain' else ''
            return f"```{lang}\n{code}\n```"
        except:
            return ''
    
    def replace_image(m):
        raw_val = m.group(1)
        val = urllib.parse.unquote(raw_val)
        if val.startswith('data:'): val = val[5:]
        try:
            d = json.loads(val)
            src = d.get('src', '')
            link = d.get('link', '')
            if src.startswith('data:'):
                return '[图片]'
            if link:
                return f"[![图片]({src})]({link})"
            return f"![图片]({src})"
        except:
            return '[图片]'
    
    # Process the raw body (URL-encoded)
    # First replace codeblocks and images
    result = re.sub(r'<card[^>]*name="codeblock"[^>]*value="([^"]*)"[^>]*>', replace_codeblock, body)
    result = re.sub(r'<card[^>]*name="image"[^>]*value="([^"]*)"[^>]*>', replace_image, result)
    result = re.sub(r'<card[^>]*>.*?</card>', '', result, flags=re.DOTALL)
    result = re.sub(r'</?card[^>]*>', '', result)
    
    # Now URL-decode
    result = urllib.parse.unquote(result)
    
    # Remove HTML tags but keep code blocks
    lines = []
    # Split into sections: code blocks and text
    parts = re.split(r'(```.*?```)', result, flags=re.DOTALL)
    for part in parts:
        if part.startswith('```'):
            # Code block - keep as-is
            lines.append(part)
        else:
            # Regular text - extract from <p> tags or top-level text
            # Remove remaining HTML tags
            text = re.sub(r'<[^>]+>', ' ', part)
            text = text.replace('&amp;','&').replace('&lt;','<').replace('&gt;','>').replace('&nbsp;',' ')
            text = re.sub(r'\s+', ' ', text).strip()
            if text and len(text) > 2:
                lines.append(text)
    
    return '\n\n'.join(lines)

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
    
    if not result:
        print(f"  [{idx}] FAIL: 读档失败 - {entry['title'][:50]}")
        results.append({'idx': idx, 'status': 'FAIL', 'title': entry['title'][:50]})
        continue
    
    body = result.get('body', '')
    markdown = body_to_markdown(body)
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
    
    page = mcpcall('notion-api.API-post-page', {
        'parent': {'type': 'database_id', 'database_id': NOTION_DATABASE_ID},
        'properties': props
    })
    
    if not page or not page.get('id'):
        print(f"  [{idx}] FAIL: 创建页失败 - {entry['title'][:50]}")
        results.append({'idx': idx, 'status': 'FAIL', 'title': entry['title'][:50]})
        continue
    
    if markdown:
        if len(markdown) > 50000:
            markdown = markdown[:50000] + "\n\n...（内容过长已截断）"
        mcpcall('notion-api.API-update-page-markdown', {
            'page_id': page['id'],
            'type': 'replace_content',
            'replace_content': {'new_str': markdown, 'allow_deleting_content': True}
        })
    
    tag_str = '/'.join(tags[:6])
    if len(tags) > 6: tag_str += f"...(+{len(tags)-6})"
    print(f"  [{idx}] OK | {entry['category']:8s} | {len(markdown):5d}字 | TAGS: {tag_str}")
    print(f"        原文链接: https://www.yuque.com/yehuoshun/twpgac/{result.get('slug', '')}")
    if url:
        print(f"        备注: 原文出处: {url}")
    results.append({'idx': idx, 'status': 'OK', 'title': entry['title'][:50], 'words': len(markdown), 'tags': len(tags)})
    time.sleep(0.3)

# Save progress
with open(PROGRESS_FILE, 'w') as f:
    f.write(str(end_idx))

ok = sum(1 for r in results if r['status'] == 'OK')
fail = sum(1 for r in results if r['status'] == 'FAIL')
print(f"\nDONE: {start_idx}-{end_idx-1} | OK={ok} FAIL={fail}")
print(f"NEXT: {end_idx}" if end_idx < len(entries) else "ALL_DONE")