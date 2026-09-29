#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""刀6 离线单测：语雀卡片转换（math/hr/file/bookmark/video/未知卡片不许静默丢）

用法：python3 tests/test_cards.py
"""
import json
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
from lake_convert import lake_to_markdown, count_unsupported_cards, UNSUPPORTED_MARK  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(('  ✅ ' if cond else '  ❌ ') + f'{name}' + (f' | {detail}' if detail else ''))


def card(name, obj=None, ctype='inline', closed=True):
    v = 'data:' + urllib.parse.quote(json.dumps(obj or {}))
    tag = f'<card type="{ctype}" name="{name}" value="{v}">'
    return tag + ('</card>' if closed else '')


def conv(inner):
    md, _ = lake_to_markdown(f'<p>前文</p>{inner}<p>后文</p>')
    return md


print('== 1. math 卡片：按内容分流（LaTeX → 公式；否则行内代码） ==')
md = conv(card('math', {'code': '\\frac{a}{b}=\\sum_{i=1}^{n} i'}))
check('LaTeX 行内 → $...$', '$\\frac{a}{b}=\\sum_{i=1}^{n} i$' in md, md.replace('\n', '⏎'))

md = conv(card('math', {'code': '\\int_0^1 x^2 dx'}, 'block'))
check('LaTeX 块 → $$...$$', '$$\n\\int_0^1 x^2 dx\n$$' in md, md.replace('\n', '⏎'))

md = conv(card('math', {'code': 'var x = {addition.NodeType};'}))
check('普通代码 → 行内反引号', '`var x = {addition.NodeType};`' in md, md.replace('\n', '⏎'))
check('普通代码不误判成公式', '$' not in md.split('前文')[1].split('后文')[0].strip('$'), '')

print('\n== 2. hr → 分隔线 ==')
check('有 value 的 hr → ---', '---' in conv(card('hr', {})), '')
check('无 value 的 hr → ---', '---' in conv('<card type="block" name="hr"></card>'), '')

print('\n== 3. 附件 / 书签 / 视频 ==')
md = conv(card('file', {'name': '报告.pdf', 'url': 'https://x.test/a.pdf'}))
check('附件 → 📎 链接', '📎 [报告.pdf](https://x.test/a.pdf)' in md, md.replace('\n', '⏎'))
md = conv(card('file', {'name': '无链附件'}))
check('附件无链接 → 可见文本（不消失）', '📎' in md and '无链附件' in md, md.replace('\n', '⏎'))
md = conv(card('bookmark', {'title': '某文章', 'url': 'https://x.test/post'}))
check('书签 → 链接', '[某文章](https://x.test/post)' in md, md.replace('\n', '⏎'))
md = conv(card('bookmark', {}))
check('书签无链接 → 可见占位', UNSUPPORTED_MARK in md, md.replace('\n', '⏎'))
md = conv(card('video', {'url': 'https://x.test/v.mp4'}))
check('视频 → 链接', '[视频](https://x.test/v.mp4)' in md, md.replace('\n', '⏎'))

print('\n== 4. 表格 / 画板 / 思维导图 → 可见占位（不静默） ==')
for nm in ('lakesheet', 'table', 'board', 'mindmap'):
    md = conv(card(nm, {'name': f'{nm}名字'}))
    check(f'{nm} → 占位且带线索', UNSUPPORTED_MARK in md and nm in md, md.replace('\n', '⏎')[:70])

print('\n== 5. 未知卡片（未来新类型）也不许静默消失 ==')
md = conv(card('zzz_brand_new', {'text': '神秘内容'}))
check('未知类型 → 可见占位', UNSUPPORTED_MARK in md and 'zzz_brand_new' in md, md.replace('\n', '⏎'))
check('带可读线索', '神秘内容' in md, '')
md = conv('<card type="inline" name="weird" value="not-json-at-all">')
check('value 非 JSON 也不炸且可见', UNSUPPORTED_MARK in md and 'weird' in md, md.replace('\n', '⏎'))
md = conv('<card type="block" name="selfclose"></card>')
check('无 value 未知卡片 → 占位', UNSUPPORTED_MARK in md, md.replace('\n', '⏎'))
md, _ = lake_to_markdown('<p>x</p><card type="inline" name="nolabel">')
check('自闭合（无 </card>）也可见', UNSUPPORTED_MARK in md, md.replace('\n', '⏎'))

print('\n== 6. 零残留 + 统计 ==')
all_cards = ''.join([
    card('math', {'code': '\\alpha^2'}), card('hr', {}),
    card('file', {'name': 'a.txt', 'url': 'https://x/a.txt'}),
    card('lakesheet', {'name': 'T'}), card('zzz', {'text': 'q'}),
])
md, _ = lake_to_markdown('<p>x</p>' + all_cards)
check('无 <card 残留', '<card' not in md and '</card>' not in md, '')
check('统计未转换类型', count_unsupported_cards(md) == {'lakesheet': 1, 'zzz': 1},
      str(count_unsupported_cards(md)))
check('已转换的不计入未转换统计', 'math' not in count_unsupported_cards(md), '')

print('\n== 7. 真实文档回归：出现过 type 都有对应产物，无静默丢失 ==')
doc_path = '/home/admin/.openclaw/workspace/tmp/doc_269354748.json'
if os.path.exists(doc_path):
    body = json.load(open(doc_path))['body']
    names = re.findall(r'<card[^>]*name="([a-zA-Z_]+)"', body)
    md, imgs = lake_to_markdown(body)
    check('无 <card 残留', '<card' not in md, '')
    check('codeblock 有输出（fence）', names.count('codeblock') == 0 or '```' in md, f'cards={set(names)}')
    check('image 有输出（占位/链接）', names.count('image') == 0 or '![图片]' in md, str(len(imgs)))
    check('内容行零丢失（相比 body 可读文本）',
          'inset：如果没有指定inset' in md, '')
else:
    check('真实文档存在', False, doc_path)

print('\n== 8. 实体解码 + 多行不塞行内 ==')
md = conv(card('math', {'code': 'Console.WriteLine(&quot;x&quot;); &amp; more'}))
check('math 内容里实体已解码', '"x"' in md and '&quot;' not in md and '& more' in md, md.replace('\n', '⏎'))
md, _ = lake_to_markdown('<p>x</p>' + '<card type="inline" name="codeblock" value="data:%7B%22code%22%3A%22a%20%26amp%3B%20b%22%2C%22mode%22%3A%22python%22%7D"></card>')
check('codeblock 内容里实体已解码', 'a & b' in md and '&amp;' not in md, md.replace('\n', '⏎'))

md = conv(card('math', {'code': 'line1\nline2\nline3'}))
check('多行非 LaTeX → 围栏（不是行内反引号）', '```\nline1\nline2\nline3\n```' in md, md.replace('\n', '⏎'))
check('多行不出现跨行行内代码',
      not re.search(r'`[^`]*\n[^`]*`', re.sub(r'```.*?```', '', md, flags=re.S)),
      md.replace('\n', '⏎'))

md = conv(card('math', {'code': '\\alpha\n\\beta'}, 'inline'))
check('多行 LaTeX 行内卡 → 独立 $$ 块', '$$\n\\alpha\n\\beta\n$$' in md, md.replace('\n', '⏎'))
check('单行 LaTeX 仍是行内 $', '$\\alpha$' in conv(card('math', {'code': '\\alpha'})), '')

print(f'\n{"=" * 52}\n通过 {len(PASS)} / 失败 {len(FAIL)}')
for x in FAIL:
    print('  ❌ ' + x)
sys.exit(1 if FAIL else 0)