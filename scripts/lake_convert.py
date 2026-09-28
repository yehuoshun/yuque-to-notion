import json, re, urllib.parse, subprocess, sys, hashlib, os, requests, base64
from cli import read_env_file, load_settings, DEFAULT_ENV_FILE, REPO_ROOT

def decode_card_value(raw_val):
    """解码 card 的 value 属性：URL解码 → 去 data: 前缀 → JSON parse"""
    val = urllib.parse.unquote(raw_val)
    if val.startswith('data:'):
        val = val[5:]
    try:
        return json.loads(val)
    except Exception:
        return None

LANG_MAP = {'markdown': 'markdown', 'md': 'markdown', 'css': 'css',
            'python': 'python', 'javascript': 'javascript', 'js': 'javascript',
            'java': 'java', 'shell': 'bash', 'bash': 'bash', 'plain': '',
            'text': '', 'html': 'html', 'json': 'json', 'sql': 'sql',
            'go': 'go', 'rust': 'rust', 'c': 'c', 'cpp': 'cpp', 'c++': 'cpp',
            'typescript': 'typescript', 'ts': 'typescript', 'yaml': 'yaml',
            'xml': 'xml', 'php': 'php', 'ruby': 'ruby', 'swift': 'swift',
            'kotlin': 'kotlin', 'scala': 'scala', 'dockerfile': 'dockerfile',
            'groovy': 'groovy'}

# ── GitHub 图床配置 ──
GITHUB_REPO = 'yehuoshun/notion-images'
GITHUB_BRANCH = 'main'
GITHUB_PAT = None
IMG_CACHE_FILE = None
_img_cache = {}  # md5 -> url

def _load_env():
    global GITHUB_PAT
    if GITHUB_PAT:
        return
    # 凭据文件路径不再写死：环境变量 > 默认位置
    path = os.environ.get('OPENCLAW_CREDENTIALS') or DEFAULT_ENV_FILE
    creds = read_env_file(path)
    GITHUB_PAT = creds.get('GITHUB_PAT_CLASSIC', '') or os.environ.get('GITHUB_PAT_CLASSIC', '')

def init_image_uploader(cache_file=None):
    """初始化上传器：加载缓存。返回 None"""
    global IMG_CACHE_FILE, _img_cache
    if cache_file is None:
        cache_file = os.environ.get('YUQUE_IMG_CACHE') or os.path.join(REPO_ROOT, 'yuque-images-cache.json')
    IMG_CACHE_FILE = cache_file
    _load_env()
    if os.path.exists(cache_file):
        try:
            with open(cache_file) as f:
                _img_cache.update(json.load(f))
        except Exception:
            pass

def upload_base64_image(b64_data, ext='png'):
    """上传 base64 图片到 GitHub 图床，返回公网 URL。
    相同内容（md5）自动去重。ext: png/jpg/gif/webp"""
    global _img_cache
    if not GITHUB_PAT:
        _load_env()
    try:
        raw = base64.b64decode(b64_data)
    except Exception:
        return None
    if not raw:
        return None
    md5 = hashlib.md5(raw).hexdigest()
    if md5 in _img_cache:
        return _img_cache[md5]
    path = f'images/{md5}.{ext}'
    url = f'https://cdn.jsdelivr.net/gh/{GITHUB_REPO}@{GITHUB_BRANCH}/{path}'
    # 上传（PUT contents API）
    headers = {'Authorization': f'token {GITHUB_PAT}', 'Accept': 'application/vnd.github+json'}
    r = requests.put(
        f'https://api.github.com/repos/{GITHUB_REPO}/contents/{path}',
        headers=headers,
        json={'message': f'add {path}', 'content': b64_data},
        timeout=60)
    if r.status_code in (200, 201):
        _img_cache[md5] = url
        _save_cache()
        return url
    if r.status_code == 422:
        # 文件已存在：那 URL 就是可用的
        _img_cache[md5] = url
        _save_cache()
        return url
    print(f'    [img] 上传失败 {r.status_code}: {r.text[:150]}')
    return None

def _save_cache():
    if IMG_CACHE_FILE:
        try:
            with open(IMG_CACHE_FILE, 'w') as f:
                json.dump(_img_cache, f, ensure_ascii=False)
        except Exception:
            pass

def _ext_from_src(src):
    m = re.search(r'\.(png|jpe?g|gif|webp|svg|bmp)', src, re.I)
    if m:
        e = m.group(1).lower()
        return 'jpg' if e == 'jpeg' else e
    if src.startswith('data:image/'):
        m2 = re.search(r'data:image/(\w+)', src)
        if m2:
            return 'png' if m2.group(1) == 'jpeg' else m2.group(1).lower()
    return 'png'

def lake_to_markdown(body):
    if not body:
        return "", []

    code_blocks = []
    images = []  # (src, link, ext)

    # ── 第一步：替换 card 为纯文本占位符 ──
    def replace_codeblock(m):
        card_data = decode_card_value(m.group(1))
        if not card_data:
            return ''
        code = card_data.get('code', '')
        mode = card_data.get('mode', 'text')
        if '%' in code:
            code = urllib.parse.unquote(code)
        lang = LANG_MAP.get(mode, mode if len(mode) <= 15 else '')
        # 嵌套代码块（code 内部含 ```）会让 Notion markdown 解析错乱吞掉图片行：
        # 直接展开不包外层 fence，让内部 ``` 成为顶层代码块
        if '```' in code:
            lines = code.split('\n')
            # 去掉外层围栏头尾（如 ```markdown ... ```）
            if lines and lines[0].strip().startswith('```'):
                lines = lines[1:]
            if lines and lines[-1].strip() == '```':
                lines = lines[:-1]
            # 配对检查：奇数个 fence → 从后往前删孤立行
            fence_count = sum(1 for ln in lines if ln.strip().startswith('```'))
            if fence_count % 2 == 1:
                for i in range(len(lines) - 1, -1, -1):
                    if lines[i].strip().startswith('```'):
                        del lines[i]
                        break
            return '\n' + '\n'.join(lines) + '\n'
        code_blocks.append((lang, code))
        return f'\n\x01CODEBLOCK{len(code_blocks)-1}\x01\n'

    def replace_image(m):
        card_data = decode_card_value(m.group(1))
        if not card_data:
            return '\n[图片]\n'
        src = card_data.get('src', '')
        link = card_data.get('link', '')
        images.append((src, link, _ext_from_src(src)))
        return f'\n\x01IMAGE{len(images)-1}\x01\n'

    result = re.sub(
        r'<card[^>]*name="codeblock"[^>]*value="([^"]*)"[^>]*>\s*</card>',
        replace_codeblock, body)
    result = re.sub(
        r'<card[^>]*name="image"[^>]*value="([^"]*)"[^>]*>\s*</card>',
        replace_image, result)
    result = re.sub(r'</?card[^>]*>', '', result)

    # ── 第二步：URL 解码 ──
    result = urllib.parse.unquote(result)

    # ── 第三步：HTML → Markdown ──
    result = re.sub(r'<h1[^>]*>(.*?)</h1>', lambda m: f"\n# {strip_tags(m.group(1))}\n", result, flags=re.DOTALL)
    result = re.sub(r'<h2[^>]*>(.*?)</h2>', lambda m: f"\n## {strip_tags(m.group(1))}\n", result, flags=re.DOTALL)
    result = re.sub(r'<h3[^>]*>(.*?)</h3>', lambda m: f"\n### {strip_tags(m.group(1))}\n", result, flags=re.DOTALL)
    result = re.sub(r'<h4[^>]*>(.*?)</h4>', lambda m: f"\n#### {strip_tags(m.group(1))}\n", result, flags=re.DOTALL)
    result = re.sub(r'<h5[^>]*>(.*?)</h5>', lambda m: f"\n##### {strip_tags(m.group(1))}\n", result, flags=re.DOTALL)
    result = re.sub(r'<h6[^>]*>(.*?)</h6>', lambda m: f"\n###### {strip_tags(m.group(1))}\n", result, flags=re.DOTALL)
    result = re.sub(r'<ul[^>]*>(.*?)</ul>', lambda m: list_to_markdown(m.group(1)), result, flags=re.DOTALL)
    result = re.sub(r'<ol[^>]*>(.*?)</ol>', lambda m: list_to_markdown(m.group(1), ordered=True), result, flags=re.DOTALL)
    result = re.sub(r'<(strong|b)[^>]*>(.*?)</\1>', r'**\2**', result, flags=re.DOTALL)
    result = re.sub(r'<(em|i)[^>]*>(.*?)</\1>', r'*\2*', result, flags=re.DOTALL)
    result = re.sub(r'<(del|s|strike)[^>]*>(.*?)</\1>', r'~~\2~~', result, flags=re.DOTALL)
    result = re.sub(r'<a[^>]+href="([^"]*)"[^>]*>(.*?)</a>', r'[\2](\1)', result, flags=re.DOTALL)
    result = re.sub(r'<p[^>]*>', '\n\n', result)
    result = re.sub(r'</p>', '\n\n', result)
    result = re.sub(r'<br\s*\/?>', '\n', result)
    result = re.sub(r'<hr\s*\/?>', '\n---\n', result)
    result = re.sub(r'<code[^>]*>(.*?)</code>', r'`\1`', result, flags=re.DOTALL)
    result = re.sub(r'<blockquote[^>]*>(.*?)</blockquote>', lambda m: f"\n> {strip_tags(m.group(1))}\n", result, flags=re.DOTALL)
    result = re.sub(r'<[^>]+>', '', result)

    # ── 第四步：清理实体 ──
    result = result.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    result = result.replace('&nbsp;', ' ').replace('&quot;', '"').replace('&#39;', "'")
    result = result.replace('\u200b', '').replace('\xa0', ' ')
    result = re.sub(r'[ \t]+\n', '\n', result)
    result = re.sub(r'\n{3,}', '\n\n\n', result)

    # ── 第五步：还原占位符（含图片上传） ──
    def restore_codeblock(m):
        idx = int(m.group(1))
        lang, code = code_blocks[idx]
        lang_str = f'{lang}\n' if lang else ''
        return f'```{lang_str}{code}\n```'

    def restore_image(m):
        idx = int(m.group(1))
        src, link, ext = images[idx]
        if not src:
            return '[图片]'
        if src.startswith('data:image/'):
            # base64 图片：占位 URL，由 batch_migrate 原生上传后 PATCH 成 file_upload
            return f'![图片](https://n2m.local/img_{idx}.{ext})'
        if link:
            return f'[![图片]({src})]({link})'
        return f'![图片]({src})'

    result = re.sub(r'\x01CODEBLOCK(\d+)\x01', restore_codeblock, result)
    result = re.sub(r'\x01IMAGE(\d+)\x01', restore_image, result)

    return result.strip(), [(src, link, ext) for src, link, ext in images]

def strip_tags(html):
    return re.sub(r'<[^>]+>', '', html).strip()

def list_to_markdown(inner, ordered=False):
    items = re.findall(r'<li[^>]*>(.*?)</li>', inner, re.DOTALL)
    lines = []
    for i, item in enumerate(items):
        text = strip_tags(item).strip()
        if text:
            lines.append(f"{i+1}. {text}" if ordered else f"- {text}")
    return '\n' + '\n'.join(lines) + '\n' if lines else ''

if __name__ == '__main__':
    init_image_uploader()
    # 用法：python3 lake_convert.py [doc_id] [--book-id xxx] ...
    # 纯数字参数当 doc_id，其余交给 cli（支持 --book-id / --print-config 等）
    cli_argv = [a for a in sys.argv[1:] if not a.isdigit()]
    S = load_settings(cli_argv, prog='lake_convert', require=('book',))
    doc_id = next((a for a in sys.argv[1:] if a.isdigit()), '269354748')  # 默认取示例文档
    cmd = ['mcporter', 'call', 'yuque-mcp.yuque_web_get_doc',
           '--args', json.dumps({'id': doc_id, 'book_id': S.book_id}),
           '--output', 'json']
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=S.mcp_cwd)
    doc = json.loads(r.stdout)
    body = doc.get('body', '')
    print(f"=== {doc.get('title')} ===")
    md, imgs = lake_to_markdown(body)
    print(f"=== markdown ({len(md)} chars), images={len(imgs)} ===")
    print(md)