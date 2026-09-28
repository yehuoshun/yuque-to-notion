#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""刀5 离线单测：拆分器（围栏保护 / 引言保留 / 深度 / 超长安全切分 / 零丢失）

用法：python3 tests/test_split.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))
os.environ['YUQUE2NOTION_STATE_DIR'] = tempfile.mkdtemp(prefix='y2n-sp-')
os.environ['NOTION_TOKEN'] = 'test'

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(('  ✅ ' if cond else '  ❌ ') + f'{name}' + (f' | {detail}' if detail else ''))


REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts')
src = open(os.path.join(REPO, 'batch_migrate.py')).read()
head = src.split('# ── 主流程 ──')[0]
ns = {'__name__': 'h'}
exec(compile(head, 'batch_migrate.py:head', 'exec'), ns)
split = ns['split_markdown_by_headings']


def fences_paired(text):
    """每片内围栏必须配对（偶数个开/闭）"""
    n = 0
    for ln in text.split('\n'):
        if ns['_fence_open'](ln):
            n += 1
        elif ln.strip().startswith('```') or ln.strip().startswith('~~~'):
            n += 1
    return n % 2 == 0


def nonempty_lines(text):
    return [l for l in text.split('\n') if l.strip()]


print('== 1. 围栏保护：代码块里的 ## 不当标题 ==')
md = ('## 第一节\n\n正文一\n\n```bash\n## 这是注释不是标题\n'
      'echo hi\n```\n\n## 第二节\n\n正文二\n') + 'x' * 30
parts = split(md, threshold=60, min_chunk=1)
check('切出多片', len(parts) >= 2, str(len(parts)))
check('每片围栏配对', all(fences_paired(c) for _, c in parts),
      str([fences_paired(c) for _, c in parts]))
check('代码块未从中间切断：含 echo 的那片同时含开围栏',
      any('```bash' in c and 'echo hi' in c for _, c in parts), '')
check('各片拼回后含全部原文非空行',
      set(nonempty_lines(md)) <= {l for _, c in parts for l in nonempty_lines(c)},
      str(set(nonempty_lines(md)) - {l for _, c in parts for l in nonempty_lines(c)}))

print('\n== 2. 首个标题之前的内容必须保留（引言不丢） ==')
md2 = ('这是引言段落，标题之前的内容。\n\n第二行引言。\n\n## 第一章\n\n'
       + '第一章正文内容。' * 20 + '\n\n## 第二章\n\n' + '第二章正文内容。' * 20)
parts2 = split(md2, threshold=100, min_chunk=1)
check('第 0 片就是引言', '引言' in parts2[0][1] and '第一章正文' not in parts2[0][1],
      parts2[0][1][:30])
check('引言片无标题（None）', parts2[0][0] is None, str(parts2[0][0]))
check('引言没被并进后面章节', all('引言段落' not in c for t, c in parts2[1:]), '')

print('\n== 3. 标题深度：h2~h6 拆，h1 不拆 ==')
md3 = ('# 文档大标题\n\n' + 'A' * 80 + '\n\n'
       '#### 四级小节\n\n' + 'B' * 80 + '\n\n'
       '###### 六级小节\n\n' + 'C' * 80 + '\n\n'
       '####### 七个井号不是标题\n\n' + 'D' * 80)
p3 = split(md3, threshold=50, min_chunk=1)
titles3 = [t for t, _ in p3]
check('#### 被拆出来', any(t == '四级小节' for t in titles3), str(titles3))
check('###### 被拆出来', any(t == '六级小节' for t in titles3), str(titles3))
check('h1 不当切点（留在引言片）', not any(t == '文档大标题' for t in titles3), str(titles3))
check('七个井号不误判为标题', not any(t and t.startswith('七个') for t in titles3), str(titles3))

print('\n== 4. 巨型代码块：切成多片且一字不丢、围栏都配对 ==')
big_code = '\n'.join(f'line_{i} = "payload_{i}"' for i in range(3000))   # ~80KB
md4 = f'## 大代码块章节\n\n```python\n{big_code}\n```\n\n收尾段落。'
orig_len = len(md4)
p4 = split(md4, threshold=20000, min_chunk=1)
check('切成多片', len(p4) >= 4, str(len(p4)))
check('每片围栏配对', all(fences_paired(c) for _, c in p4), str([fences_paired(c) for _, c in p4]))
check('每片 ≤ 阈值', all(len(c) <= 20000 for _, c in p4), str([len(c) for _, c in p4]))
missing = set(nonempty_lines(md4)) - {l for _, c in p4 for l in nonempty_lines(c)}
check('零丢失：原文每一非空行都在某片里', not missing, str(list(missing)[:3]))
check('代码行数与原文一致',
      sum(1 for _, c in p4 for l in c.split('\n') if l.startswith('line_')) == 3000,
      str(sum(1 for _, c in p4 for l in c.split('\n') if l.startswith('line_'))))
check('重开的围栏保留了语言', sum(1 for _, c in p4 for l in c.split('\n') if l.strip() == '```python') >= 2,
      str(sum(1 for _, c in p4 for l in c.split('\n') if l.strip() == '```python')))

print('\n== 5. 无标题的超长文档：按行安全切分，不再硬截断 ==')
md5 = '段落内容。\n' * 5000     # 无标题，远超阈值
p5 = split(md5, threshold=10000, min_chunk=1)
check('无标题也能切', len(p5) > 1, str(len(p5)))
check('无 "已截断" 字样', all('已截断' not in c for _, c in p5), '')
check('每片 ≤ 阈值', all(len(c) <= 10000 for _, c in p5), str([len(c) for c in map(lambda x: x[1], p5)][:5]))
check('内容零丢失', sum(nonempty_lines(c).__len__() for _, c in p5) == 5000,
      str(sum(len(nonempty_lines(c)) for _, c in p5)))

print('\n== 6. 极端：单行超长（无法按行切）原样保留，由上层记账 ==')
md6 = '## 一坨\n\n' + 'Z' * 30000
p6 = split(md6, threshold=5000, min_chunk=1)
check('不丢内容（原样保留超长片）', any(len(c) >= 30000 for _, c in p6), str([len(c) for _, c in p6]))

print('\n== 7. 短章节并入上一节，但第 0 片不被吞 ==')
md7 = ('引言' + 'i' * 50 + '\n\n## 大节\n\n' + 'X' * 300 + '\n\n## 小节\n\n短\n')
p7 = split(md7, threshold=100, min_chunk=50)
check('引言仍是第 0 片', p7[0][1].startswith('引言'), p7[0][1][:10])
check('短章节被并入上一节', not any(t == '小节' for t, _ in p7), str([t for t, _ in p7]))
check('并入后内容还在', any('短' in c for _, c in p7), '')

print('\n== 8. 真实文档回归：逐行比对不丢内容 ==')
from lake_convert import lake_to_markdown  # noqa: E402
doc_path = '/home/admin/.openclaw/workspace/tmp/doc_269354748.json'
if os.path.exists(doc_path):
    body = json.load(open(doc_path))['body']
    md, _ = lake_to_markdown(body)
    for th in (500, 2000, 100000):
        pp = split(md, threshold=th, min_chunk=1)
        missing = set(nonempty_lines(md)) - {l for _, c in pp for l in nonempty_lines(c)}
        ok_pair = all(fences_paired(c) for _, c in pp)
        check(f'真实文档 threshold={th}：零丢失 + 围栏配对', not missing and ok_pair,
              f'parts={len(pp)} missing={len(missing)} pair={ok_pair}')
else:
    check('真实文档存在', False, doc_path)

print('\n== 9. 端到端：拆分后每片 ≤ 阈值（截断分支走不到） ==')
md9 = ('## A\n\n' + 'a\n' * 1000 + '\n## B\n\n' + 'b\n' * 1000 + '\n```py\n' + 'c\n' * 1000 + '```')
p9 = split(md9, threshold=2000, min_chunk=1)
# 容差 512：断点不在围栏收尾行之前切（否则会造出空代码块），允许溢出一行
check('每片 ≤ 阈值(+512 容差)', all(len(c) <= 2000 + 512 for _, c in p9), str([len(c) for _, c in p9]))
check('每片围栏配对', all(fences_paired(c) for _, c in p9), '')
content_lines = sum(1 for _, c in p9 for l in c.split('\n') if l in ('a', 'b', 'c'))
check('内容零丢失（3000 行正文）', content_lines == 3000, str(content_lines))
check('注入的围栏行不计入正文', sum(len(nonempty_lines(c)) for _, c in p9) == 3000 + 6,
      str(sum(len(nonempty_lines(c)) for _, c in p9)))

print(f'\n{"=" * 52}\n通过 {len(PASS)} / 失败 {len(FAIL)}')
for x in FAIL:
    print('  ❌ ' + x)
sys.exit(1 if FAIL else 0)