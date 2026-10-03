#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""刀9 离线集成测试：大文档拆分收尾
  ① 去重按「页数 + 标题」对账，缺片补建（不再整篇 SKIP）
  ② 每片只上传本片用到的图片（不再 N 片 × M 图 重复上传）

手段同 test_cursor：假 mcporter + sitecustomize 拦网，真跑 batch_migrate.py。
用法：python3 tests/test_bigdoc.py
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


FAKE_MCPORTER = '''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
tool = args[1] if args and args[0] == 'call' else (args[0] if args else '')
a = {}
if '--args' in args:
    a = json.loads(args[args.index('--args') + 1])
if tool.endswith('yuque_web_get_doc'):
    doc_id = str(a.get('id'))
    d = os.environ.get('FAKE_BODY_DIR', '')
    p = os.path.join(d, doc_id + '.json') if d else ''
    if p and os.path.exists(p):
        body = json.load(open(p))
    else:
        body = '<p>正文' + doc_id + '</p>'
    print(json.dumps({'id': doc_id, 'title': 'T' + doc_id, 'slug': 's' + doc_id,
                      'body': body, 'word_count': len(body)}))
else:
    print(json.dumps({'data': []}))
'''

SITECUSTOMIZE = '''import json, os
CREATED = os.environ.get('FAKE_CREATED_FILE', '')
UPLOADS = os.environ.get('FAKE_UPLOAD_FILE', '')
_titles = {}   # 本进程内 page-id -> title
_existing = json.loads(os.environ.get('FAKE_EXISTING', '[]'))

class Resp:
    def __init__(self, code, body=None):
        self.status_code = code
        self._body = body if body is not None else {}
        self.text = json.dumps(self._body)
    def json(self):
        return self._body

_n = {'pages': 0, 'up': 0}

def _log(path, rec):
    if path:
        with open(path, 'a') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + chr(10))

def _route(method, url, **kw):
    if '/data_sources/' in url and url.rstrip('/').endswith('/query'):
        results = [{'id': p['id'], 'properties': {'名称': {'type': 'title',
                   'title': [{'plain_text': p['title']}]}}} for p in _existing]
        return Resp(200, {'results': results, 'has_more': False})
    if method == 'GET' and '/v1/databases/' in url:
        return Resp(200, {'id': 'db', 'data_sources': [{'id': 'ds-1', 'name': 'x'}]})
    if method == 'GET' and '/v1/data_sources/' in url:
        return Resp(200, {'id': 'ds-1', 'properties': {'原文链接': {'type': 'url'}}})
    if method == 'POST' and url.endswith('/v1/pages'):
        _n['pages'] += 1
        pid = 'page-%d' % _n['pages']
        props = (kw.get('json') or {}).get('properties') or {}
        title = ''
        for v in props.values():
            if isinstance(v, dict) and 'title' in v:
                title = ''.join(t.get('text', {}).get('content', '') for t in v['title'])
        _log(CREATED, {'id': pid, 'title': title})
        return Resp(200, {'id': pid})
    if method == 'PATCH' and '/markdown' in url:
        return Resp(200, {})
    if method == 'GET' and '/v1/blocks/' in url:
        urls = json.loads(os.environ.get('FAKE_BLOCK_URLS', '[]'))
        return Resp(200, {'results': [{'id': 'b%d' % i, 'type': 'image',
                                       'image': {'external': {'url': u}}} for i, u in enumerate(urls)]})
    if method == 'PATCH' and '/v1/blocks/' in url:
        return Resp(200, {})
    if method == 'POST' and '/v1/file_uploads' in url:
        _n['up'] += 1
        _log(UPLOADS, {'n': _n['up'], 'json': kw.get('json')})
        return Resp(200, {'id': 'fid-%d' % _n['up'], 'upload_url': 'https://up.invalid/x'})
    if method == 'POST' and url.startswith('https://up.invalid'):
        return Resp(200, {})
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

DB_ID = __import__('config').NOTION_DATABASE_ID if 'config' in dir() else None


def _cfg():
    import importlib
    import config as c
    importlib.reload(c)
    return c


class Env:
    def __init__(self, doc_id=1000, title='大文档'):
        self.root = tempfile.mkdtemp(prefix='y2n-big-')
        self.state = os.path.join(self.root, 'state')
        self.bindir = os.path.join(self.root, 'bin')
        self.sitedir = os.path.join(self.root, 'site')
        self.bodies = os.path.join(self.root, 'bodies')
        for d in (self.state, self.bindir, self.sitedir, self.bodies):
            os.makedirs(d, exist_ok=True)
        p = os.path.join(self.bindir, 'mcporter')
        with open(p, 'w') as f:
            f.write(FAKE_MCPORTER)
        os.chmod(p, 0o755)
        with open(os.path.join(self.sitedir, 'sitecustomize.py'), 'w') as f:
            f.write(SITECUSTOMIZE)
        self.doc_id = doc_id
        self.title = title
        self.created_file = os.path.join(self.root, 'created.jsonl')
        self.upload_file = os.path.join(self.root, 'uploads.jsonl')
        self.block_urls = []
        with open(os.path.join(self.state, 'yuque_entries.json'), 'w') as f:
            json.dump([{'doc_id': doc_id, 'title': title, 'slug': f's{doc_id}',
                        'source': '其他', 'category': '其他'}], f, ensure_ascii=False)

    def set_body(self, html):
        with open(os.path.join(self.bodies, str(self.doc_id) + '.json'), 'w') as f:
            json.dump(html, f, ensure_ascii=False)

    def set_existing(self, pages):
        self.existing = pages

    def created(self):
        if not os.path.exists(self.created_file):
            return []
        return [json.loads(l) for l in open(self.created_file) if l.strip()]

    def uploads(self):
        if not os.path.exists(self.upload_file):
            return []
        return [json.loads(l) for l in open(self.upload_file) if l.strip()]

    def run(self):
        env = dict(os.environ)
        env['PATH'] = self.bindir + os.pathsep + env['PATH']
        env['PYTHONPATH'] = self.sitedir + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
        env['YUQUE2NOTION_STATE_DIR'] = self.state
        env['NOTION_TOKEN'] = 'fake'
        env['FAKE_BODY_DIR'] = self.bodies
        env['FAKE_CREATED_FILE'] = self.created_file
        env['FAKE_UPLOAD_FILE'] = self.upload_file
        env['FAKE_EXISTING'] = json.dumps(getattr(self, 'existing', []))
        env['FAKE_BLOCK_URLS'] = json.dumps(self.block_urls)
        # 拆分/批量参数（cli 支持 env 覆盖）
        env['MAX_CONTENT_CHARS'] = '400'
        env['SPLIT_MIN_CHUNK'] = '1'
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, 'batch_migrate.py'),
                            '--state-dir', self.state, '--book-id', 'TB'],
                           capture_output=True, text=True, env=env, cwd=REPO, timeout=180)
        return r

    def results(self):
        return st.load_results(os.path.join(self.state, 'prev_results.json'))

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


def body_3sections():
    """三段内容 → 会被拆成多片（用词组重复，避免被预检判成二进制）"""
    return ('## 第一节\n\n' + '第一节正文内容示例。' * 30
            + '\n\n## 第二节\n\n' + '第二节正文内容示例。' * 30
            + '\n\n## 第三节\n\n' + '第三节正文内容示例。' * 30)


def body_dup_sections():
    """三个同名章节「总结」+ 一个「附录」→ 4 片（片0=主页面，片1/2 子页标题会撞）；
    旧逻辑子页标题重复，对账会串页"""
    return ('## 总结\n\n' + '总结内容示例文字。' * 30
            + '\n\n## 总结\n\n' + '总结内容示例文字。' * 30
            + '\n\n## 总结\n\n' + '总结内容示例文字。' * 30
            + '\n\n## 附录\n\n' + '附录内容示例文字。' * 30)


print('== 1. 全新文档：按片数建页（拆分后 page_ids 完整） ==')
e = Env()
e.set_body(body_3sections())
r = e.run()
res = e.results()[0] if e.results() else {}
n_created = len(e.created())
check('exit 0', r.returncode == 0, r.stderr[-200:] if r.returncode else '')
check('拆成 3 片', res.get('chunks_total') == 3, str(res.get('chunks_total')))
check('建了 3 页', n_created == 3, f'created={n_created}')
check('状态 OK', res.get('status') == 'OK', str(res.get('status')))
check('page_ids 0/1/2 齐全', sorted(res.get('page_ids', {}).keys()) == ['0', '1', '2'],
      str(res.get('page_ids')))
check('子页标题 = 文档名 - 章节名',
      any(c['title'].startswith('大文档 - ') for c in e.created()), str([c['title'] for c in e.created()]))
e.cleanup()

print('\n== 2. 缺片补建：只有主页面存在时，补建其余片（旧代码会整篇 SKIP） ==')
e = Env()
e.set_body(body_3sections())
e.set_existing([{'id': 'exist-main', 'title': '大文档'}])
r = e.run()
res = e.results()[0] if e.results() else {}
check('exit 0', r.returncode == 0, '')
check('识别出已有 1/3 页', '补建缺片' in r.stdout, '')
check('补建了 2 页（不是 3 页）', len(e.created()) == 2, f'created={len(e.created())}')
check('状态 OK（片齐了）', res.get('status') == 'OK', str(res.get('status')))
check('主页面复用已有 id', res.get('page_ids', {}).get('0') == 'exist-main',
      str(res.get('page_ids', {}).get('0')))
check('page_ids 齐全', sorted(res.get('page_ids', {}).keys()) == ['0', '1', '2'], str(res.keys()))
e.cleanup()

print('\n== 3. 片齐 → 真去重 SKIP，不建页 ==')
e = Env()
e.set_body(body_3sections())
e.set_existing([{'id': 'p0', 'title': '大文档'},
                {'id': 'p1', 'title': '大文档 - 第二节'},
                {'id': 'p2', 'title': '大文档 - 第三节'}])
r = e.run()
res = e.results()[0] if e.results() else {}
check('SKIP', res.get('status') == 'SKIP', str(res.get('status')))
check('没建任何页', len(e.created()) == 0, f'created={len(e.created())}')
check('SKIP 原因 = notion_exists', res.get('reason') == 'notion_exists', str(res.get('reason')))
e.cleanup()

print('\n== 4. 每片只上传本片图片（不是每片全量重传） ==')
e = Env()
import urllib.parse
img1 = 'data:image/png;base64,' + 'A' * 200
img2 = 'data:image/png;base64,' + 'B' * 200
def icard(b64):
    return ('<card type="inline" name="image" value="data:'
            + urllib.parse.quote(json.dumps({'src': b64, 'link': ''})) + '"></card>')
e.set_body('## 第一节\n\n' + icard(img1) + '第一节正文内容示例。' * 20
           + '\n\n## 第二节\n\n' + icard(img2) + '第二节正文内容示例。' * 20)
e.block_urls = ['https://n2m.local/img_0.png', 'https://n2m.local/img_1.png']
r = e.run()
res = e.results()[0] if e.results() else {}
n_up = len(e.uploads())
check('exit 0', r.returncode == 0, r.stderr[-200:] if r.returncode else '')
check('拆成 2 片', res.get('chunks_total') == 2, str(res.get('chunks_total')))
check('总上传次数 = 2（旧逻辑会是 4：每片都传全部图）', n_up == 2, f'uploads={n_up}')
check('imgs 记账 = 2/2', res.get('imgs_ok') == 2 and res.get('imgs_total') == 2,
      f"{res.get('imgs_ok')}/{res.get('imgs_total')}")
e.cleanup()

print('\n== 5. helpers 单元 ==')
ns = {}
src = open(os.path.join(SCRIPTS, 'batch_migrate.py')).read()
head = src.split('# ── 主流程 ──')[0]
os.environ['YUQUE2NOTION_STATE_DIR'] = tempfile.mkdtemp(prefix='y2n-big-h-')
os.environ['NOTION_TOKEN'] = 't'
exec(compile(head, 'h', 'exec'), ns)
check('chunk_page_title 主页面 = 文档名', ns['chunk_page_title']('标题', 0, '') == '标题', '')
check('chunk_page_title 子页 = 文档名 - 章节（第N片）',
      ns['chunk_page_title']('标题', 1, '章节A') == '标题 - 章节A（第2片）', '')
md = '前![图片](https://n2m.local/img_0.png)中 ![图片](https://n2m.local/img_3.png)后'
imgs = [('a', '', 'png'), ('b', '', 'png'), ('c', '', 'png'), ('d', '', 'png')]
sub = ns['images_for_chunk'](md, imgs)
check('images_for_chunk 只取本片图且保留原下标', [x[0] for x in sub] == [0, 3], str([x[0] for x in sub]))

print('\n== 6. 同名章节：子页标题带序号，首次迁移不冲突 ==')
e = Env()
e.set_body(body_dup_sections())
r = e.run()
res = e.results()[0] if e.results() else {}
check('exit 0', r.returncode == 0, r.stderr[-200:] if r.returncode else '')
check('拆成 4 片', res.get('chunks_total') == 4, str(res.get('chunks_total')))
check('建了 4 页', len(e.created()) == 4, f'created={len(e.created())}')
check('状态 OK', res.get('status') == 'OK', str(res.get('status')))
titles = [c['title'] for c in e.created()]
check('两个同名章节标题带序号且不同',
      '大文档 - 总结（第2片）' in titles and '大文档 - 总结（第3片）' in titles, str(titles))
e.cleanup()

print('\n== 7. 同名旧格式页（无序号）：歧义 → 不复用，补建带序号页，旧页不覆盖 ==')
e = Env()
e.set_body(body_dup_sections())
e.set_existing([{'id': 'old0', 'title': '大文档'},
                {'id': 'old1', 'title': '大文档 - 总结'},
                {'id': 'old2', 'title': '大文档 - 总结'},
                {'id': 'old3', 'title': '大文档 - 附录'}])
r = e.run()
res = e.results()[0] if e.results() else {}
created = e.created()
check('exit 0', r.returncode == 0, r.stderr[-200:] if r.returncode else '')
check('只补建 2 页（两个歧义的总结片）', len(created) == 2, f'created={len(created)}')
check('新页标题带序号',
      sorted(c['title'] for c in created) == ['大文档 - 总结（第2片）', '大文档 - 总结（第3片）'], str(created))
check('复用主页面 old0', res.get('page_ids', {}).get('0') == 'old0', str(res.get('page_ids')))
check('复用附录旧页 old3', res.get('page_ids', {}).get('3') == 'old3', str(res.get('page_ids')))
check('两个歧义片都指向新页（非 old1/old2）',
      res.get('page_ids', {}).get('1') not in ('old1', 'old2')
      and res.get('page_ids', {}).get('2') not in ('old1', 'old2'),
      str(res.get('page_ids')))
check('状态 OK（片齐了）', res.get('status') == 'OK', str(res.get('status')))
e.cleanup()

print('\n== 8. 带序号页全在 → 精确匹配 SKIP ==')
e = Env()
e.set_body(body_dup_sections())
e.set_existing([{'id': 'p0', 'title': '大文档'},
                {'id': 'p1', 'title': '大文档 - 总结（第2片）'},
                {'id': 'p2', 'title': '大文档 - 总结（第3片）'},
                {'id': 'p3', 'title': '大文档 - 附录（第4片）'}])
r = e.run()
res = e.results()[0] if e.results() else {}
check('exit 0', r.returncode == 0, '')
check('SKIP', res.get('status') == 'SKIP', str(res.get('status')))
check('没建任何页', len(e.created()) == 0, f'created={len(e.created())}')
check('SKIP 原因 = notion_exists', res.get('reason') == 'notion_exists', str(res.get('reason')))
e.cleanup()

print('\n== 9. 标题对账 helper 单元 ==')
ns2 = {}
src2 = open(os.path.join(SCRIPTS, 'batch_migrate.py')).read()
head2 = src2.split('# ── 主流程 ──')[0]
os.environ['YUQUE2NOTION_STATE_DIR'] = tempfile.mkdtemp(prefix='y2n-big-h2-')
os.environ['NOTION_TOKEN'] = 't'
exec(compile(head2, 'h', 'exec'), ns2)
check('chunk_page_title 子页带序号（含中文括号）',
      ns2['chunk_page_title']('标题', 1, '章节A') == '标题 - 章节A（第2片）', '')
check('新格式别名 = [新, 旧]',
      ns2['_title_aliases']('标题 - 章节A（第2片）') == ('标题 - 章节A（第2片）', '标题 - 章节A'), '')
check('旧格式别名 = [旧]', ns2['_title_aliases']('标题 - 章节A') == ('标题 - 章节A',), '')

def bt():
    return {'标题 - 章节A（第2片）': ['n1'], '标题 - 章节A': ['o1', 'o2'], '标题 - 章节C': ['u1']}

check('精确匹配优先', ns2['_match_title']('标题 - 章节A（第2片）', bt()) == 'n1', '')
check('旧格式唯一可复用', ns2['_match_title']('标题 - 章节C（第2片）', bt()) == 'u1', '')
check('旧格式歧义 → None', ns2['_match_title']('标题 - 章节A（第3片）', bt()) is None, '')

print(f'\n{"=" * 52}\n通过 {len(PASS)} / 失败 {len(FAIL)}')
for x in FAIL:
    print('  ❌ ' + x)
sys.exit(1 if FAIL else 0)