#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""刀7 离线单测：图片扩展名按字节魔数嗅探

用法：python3 tests/test_images.py
"""
import base64
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
from lake_convert import _ext_from_src, lake_to_markdown  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(('  ✅ ' if cond else '  ❌ ') + f'{name}' + (f' | {detail}' if detail else ''))


def data_uri(raw, declared='png'):
    return f'data:image/{declared};base64,' + base64.b64encode(raw).decode()


def pad(n):
    return b'\x00' * max(0, n - 12)


print('== 1. 常见魔数 ==')
cases = [
    (b'\x89PNG\r\n\x1a\n' + pad(64), 'png', 'png'),
    (b'\xff\xd8\xff\xe0' + pad(64), 'jpeg', 'jpg'),
    (b'GIF89a' + pad(64), 'gif', 'gif'),
    (b'GIF87a' + pad(64), 'gif', 'gif'),
    (b'RIFF\x24\x00\x00\x00WEBPVP8 ' + pad(64), 'png', 'webp'),
    (b'BM' + pad(64), 'bmp', 'bmp'),
]
for raw, declared, want in cases:
    got = _ext_from_src(data_uri(raw, declared))
    check(f'{want} 魔数识别', got == want, f'declared={declared} got={got}')

print('\n== 2. 关键字用例：声明 png、字节是 WEBP（实测踩过） ==')
raw = b'RIFF\xe6\x0a\x00\x00WEBPVP8 ' + pad(64)
uri = data_uri(raw, 'png')
check('声明 png × 字节 webp → webp', _ext_from_src(uri) == 'webp', _ext_from_src(uri))
check('不再盲信声明', _ext_from_src(uri) != 'png', '')

print('\n== 3. SVG（文本型） ==')
check('<svg 开头 → svg', _ext_from_src(data_uri(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', 'svg+xml')) == 'svg', '')
check('<?xml 开头 → svg', _ext_from_src(data_uri(b'<?xml version="1.0"?><svg></svg>', 'svg+xml')) == 'svg', '')

print('\n== 4. 嗅不出 → 回退声明，不乱猜 ==')
weird = data_uri(b'\x00\x01\x02\x03' + pad(64), 'png')
check('未知字节回退声明 png', _ext_from_src(weird) == 'png', _ext_from_src(weird))
weird2 = data_uri(b'\x00\x01\x02\x03' + pad(64), 'gif')
check('未知字节回退声明 gif', _ext_from_src(weird2) == 'gif', _ext_from_src(weird2))
check('坏 base64 不抛异常', _ext_from_src('data:image/png;base64,!!!!') == 'png', '')

print('\n== 5. 外链图仍看 URL 后缀 ==')
check('...JPEG → jpg', _ext_from_src('https://x.test/a.JPEG') == 'jpg', '')
check('...png?x=1 → png', _ext_from_src('https://x.test/a.png?v=1') == 'png', '')
check('无后缀 → png', _ext_from_src('https://x.test/a') == 'png', '')
check('空 src → png', _ext_from_src('') == 'png', '')

print('\n== 6. 端到端：占位 URL 用嗅探出的扩展名 ==')
raw = b'RIFF\xe6\x0a\x00\x00WEBPVP8 ' + pad(80)
card = ('<card type="inline" name="image" value="data:'
        + urllib.parse.quote(json.dumps({'src': data_uri(raw, 'png'), 'link': ''})) + '"></card>')
md, imgs = lake_to_markdown(f'<p>图：</p>{card}')
check('图片元数据 ext = webp', imgs and imgs[0][3] == 'webp', str(imgs[0][3] if imgs else None))
check('占位 URL 带 .webp', 'n2m.local/img_0.webp' in md, md.replace('\n', '⏎')[:80])

print('\n== 7. 真实文档回归（那张实际是 WEBP 的图） ==')
p = '/home/admin/.openclaw/workspace/tmp/doc_269354748.json'
if os.path.exists(p):
    body = json.load(open(p))['body']
    md, imgs = lake_to_markdown(body)
    ext = imgs[0][3] if imgs else None
    check('真实图嗅探为 webp（声明是 png）', ext == 'webp', f'ext={ext}')
    check('占位 URL 与之一致', 'img_0.webp' in md, '')
else:
    check('真实文档存在', False, p)

print(f'\n{"=" * 52}\n通过 {len(PASS)} / 失败 {len(FAIL)}')
for x in FAIL:
    print('  ❌ ' + x)
sys.exit(1 if FAIL else 0)