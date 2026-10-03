import base64, json, re, urllib.parse, subprocess, sys
from cli import load_settings

def decode_card_value(raw_val):
    """解码 card 的 value 属性：URL解码 → 去 data: 前缀 → JSON parse"""
    val = urllib.parse.unquote(raw_val)
    if val.startswith('data:'):
        val = val[5:]
    try:
        return json.loads(val)
    except Exception:
        return None


# ── 卡片识别 ──
NAME_RE = re.compile(r'name="([^"]*)"', re.I)
VALUE_RE = re.compile(r'value="([^"]*)"', re.I)
TYPE_RE = re.compile(r'type="([^"]*)"', re.I)
# 任意 card 标签（含自闭合/无 value 的）
CARD_ANY_RE = re.compile(r'<card\b[^>]*>(?:\s*</card>)?', re.I | re.S)
# 公式特征：真正 LaTeX 才当公式，否则当行内代码（实测语雀 math 卡片也会装普通代码）
LATEX_HINT_RE = re.compile(r'\\[a-zA-Z]{2,}|\^\s*\{|_\s*\{|\\frac|\\sum|\\int|\\sqrt|\\begin\{')
UNSUPPORTED_MARK = '未转换的语雀卡片'


def _card_name(tag):
    m = NAME_RE.search(tag or '')
    return (m.group(1).lower() if m else 'unknown')


def _looks_latex(code):
    return bool(LATEX_HINT_RE.search(code or ''))


def _unescape(text):
    """HTML 实体还原（代码/公式内容也需要，否则会留下 &quot; &amp;）"""
    if text is None:
        return ''
    return (text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
                .replace('&nbsp;', ' ').replace('&quot;', '"').replace('&#39;', "'")
                .replace('\u200b', '').replace('\xa0', ' '))


def card_placeholder(name, hint=''):
    """未支持/降级卡片的可见占位（绝不静默丢掉）"""
    extra = f'（{hint}）' if hint else ''
    return f'\n> ⚠️ {UNSUPPORTED_MARK}：{name or "unknown"}{extra}（请回原文查看）\n'


def count_unsupported_cards(markdown):
    """统计正文里未转换的卡片类型及数量，供 batch_migrate 记账"""
    from collections import Counter
    return dict(Counter(re.findall(rf'{UNSUPPORTED_MARK}：([a-zA-Z_\-]+)', markdown or '')))

LANG_MAP = {'markdown': 'markdown', 'md': 'markdown', 'css': 'css',
            'python': 'python', 'javascript': 'javascript', 'js': 'javascript',
            'java': 'java', 'shell': 'bash', 'bash': 'bash', 'plain': '',
            'text': '', 'html': 'html', 'json': 'json', 'sql': 'sql',
            'go': 'go', 'rust': 'rust', 'c': 'c', 'cpp': 'cpp', 'c++': 'cpp',
            'typescript': 'typescript', 'ts': 'typescript', 'yaml': 'yaml',
            'xml': 'xml', 'php': 'php', 'ruby': 'ruby', 'swift': 'swift',
            'kotlin': 'kotlin', 'scala': 'scala', 'dockerfile': 'dockerfile',
            'groovy': 'groovy'}

# ── 说明：公网图床降级已移除（2026-09-28）──
# 理由：实测 Notion 原生 file_upload 几乎不失败；external 图是客户端拉取，
# 图床必须公网可读，等于把语雀私有库图片推到公网，单向不可撤回。
# 图片失败现在走 batch_migrate 的「记账 + 重试 + --resume-write 后补」。

# 图片魔数（只解 data URI 开头一小段就能判）
_IMG_MAGIC = [(b'\x89PNG\r\n\x1a\n', 'png'), (b'\xff\xd8\xff', 'jpg'),
              (b'GIF87a', 'gif'), (b'GIF89a', 'gif'), (b'RIFF', 'webp'), (b'BM', 'bmp')]


def _sniff_ext(src, declared=''):
    """按 base64 字节魔数判扩展名；判不出就回退 declared（不乱猜）。
    只解前 32 个 base64 字符（≈24 字节），不把整张图解出来。"""
    try:
        b64 = src.split(',', 1)[1] if ',' in src else ''
        head = base64.b64decode(b64[:32] + '=' * ((4 - len(b64[:32]) % 4) % 4))
    except Exception:
        return declared or 'png'
    for magic, ext in _IMG_MAGIC:
        if head.startswith(magic):
            if magic == b'RIFF' and head[8:12] != b'WEBP':
                continue
            return ext
    h = head.lstrip()
    if h.startswith(b'<svg') or h.startswith(b'<?xml') or b'<svg' in head:
        return 'svg'
    return declared or 'png'


def _ext_from_src(src):
    """扩展名：URL 图按路径后缀；data URI **按字节魔数嗅探**
    （不能只信声明的 mime：实测有声明 image/png 实际是 RIFF/WEBP 字节的）"""
    if not src:
        return 'png'
    if src.startswith('data:image/'):
        declared = ''
        m2 = re.search(r'data:image/([\w+.-]+)', src)
        if m2:
            declared = m2.group(1).lower()
            declared = {'jpeg': 'jpg', 'svg+xml': 'svg'}.get(declared, declared)
        return _sniff_ext(src, declared)
    m = re.search(r'\.(png|jpe?g|gif|webp|svg|bmp)', src, re.I)
    if m:
        e = m.group(1).lower()
        return 'jpg' if e == 'jpeg' else e
    return 'png'

def lake_to_markdown(body):
    if not body:
        return "", []

    code_blocks = []
    images = []  # (src, link, ext)
    math_cards = []  # (is_block, code)

    # ── 第一步：替换 card 为纯文本占位符 ──
    def replace_codeblock(m):
        card_data = decode_card_value(m.group(1))
        if not card_data:
            return ''
        code = card_data.get('code', '')
        mode = card_data.get('mode', 'text')
        code = _unescape(code)
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

    # ── 第一步之二：其它卡片分类处理（不许静默丢掉）──
    def replace_other_card(m):
        tag = m.group(0)
        nm = (NAME_RE.search(tag) or [None, ''])[1].lower()
        vm = VALUE_RE.search(tag)
        raw_val = vm.group(1) if vm else ''
        card_type = ((TYPE_RE.search(tag) or [None, ''])[1] or '').lower()

        if nm == 'hr':
            return '\n---\n'
        if nm in ('math', 'formula', 'tex'):
            d = decode_card_value(raw_val) or {}
            code = d.get('code', '') or ''
            code = _unescape(code).strip()
            if not code:
                return ''
            math_cards.append((card_type == 'block', code))
            return f'\n\x01MATH{len(math_cards)-1}\x01\n'

        d = decode_card_value(raw_val) or {}
        url = _unescape(str(d.get('url') or d.get('src') or d.get('link') or '')).strip()
        title = _unescape(str(d.get('name') or d.get('title') or d.get('text') or '')).strip()

        if nm in ('file', 'attachment', 'localdoc'):
            return f'\n📎 [{title or "附件"}]({url})\n' if url else f'\n📎 附件：{title or "（无链接）"}\n'
        if nm in ('bookmark', 'link', 'yuque'):
            if url:
                return f'\n[{title or url}]({url})\n'
            return card_placeholder('bookmark')
        if nm in ('video', 'iframe', 'audio'):
            if url:
                label = '音频' if nm == 'audio' else '视频'
                return f'\n[{label}]({url})\n'
            return card_placeholder(nm)

        # 表格/画板/思维导图/未如类型 → 可见占位（内容不静默消失）
        hint = _unescape((title or str(d.get('code') or '') or ''))[:60].replace('\n', ' ')
        return card_placeholder(nm, hint)

    result = re.sub(CARD_ANY_RE, replace_other_card, result)

    # 安全网：形状奇特的残留 card 也必须变成可见占位
    result = re.sub(r'<card\b[^>]*>', lambda m: card_placeholder(_card_name(m.group(0))), result)
    result = re.sub(r'</card>', '', result)

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
    result = _unescape(result)
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

    def restore_math(m):
        idx = int(m.group(1))
        is_block, code = math_cards[idx]
        multiline = '\n' in code
        if _looks_latex(code):
            # 块级或含换行 → 独立公式块（行内 $..$ 不能跨行）
            return f'\n$$\n{code}\n$$\n' if (is_block or multiline) else f'${code}$'
        # 不是 LaTeX（实测语雀 math 卡片也装普通代码）→ 代码
        if multiline:
            return f'\n```\n{code}\n```\n'
        return f'`{code}`'

    result = re.sub(r'\x01CODEBLOCK(\d+)\x01', restore_codeblock, result)
    result = re.sub(r'\x01IMAGE(\d+)\x01', restore_image, result)
    result = re.sub(r'\x01MATH(\d+)\x01', restore_math, result)

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