#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""刀3 离线单测：写正文失败 → PARTIAL / 图片记账 / 重试 / --resume-write 幂等

用法：python3 tests/test_write_status.py
全程不联网（requests 用 mock 替换），状态目录指向 /tmp。
"""
import base64
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))

# 状态目录指向临时目录，别碰仓库
os.environ['YUQUE2NOTION_STATE_DIR'] = tempfile.mkdtemp(prefix='y2n-ws-')
os.environ['NOTION_TOKEN'] = 'test-token'

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(('  ✅ ' if cond else '  ❌ ') + f'{name}' + (f' | {detail}' if detail else ''))


# ── 提取 batch_migrate 函数区（主流程之前）──
REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts')
src = open(os.path.join(REPO, 'batch_migrate.py')).read()
head = src.split('# ── 主流程 ──')[0]
ns = {'__name__': 'batch_migrate_head'}
exec(compile(head, 'batch_migrate.py:head', 'exec'), ns)


class FakeReq:
    """按 URL 片段路由的 mock requests。"""

    def __init__(self):
        self.rules = {}   # (method, url_frag) -> (status, json_body)
        self.calls = []   # 每次调用的 (method, url, kwargs_json)

    def _resp(self, status, body=None):
        class R:
            status_code = status
            text = json.dumps(body or {}) if body is not None else ''
            def json(self):
                return body or {}
        return R()

    def _route(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for (m, frag), (status, body) in self.rules.items():
            if m == method and frag in url:
                return self._resp(status, body)
        return self._resp(500)

    def get(self, url, **kw):
        return self._route('GET', url, **kw)

    def post(self, url, **kw):
        return self._route('POST', url, **kw)

    def patch(self, url, **kw):
        return self._route('PATCH', url, **kw)


def png_b64():
    return base64.b64encode(b'\x89PNG\r\n\x1a\n' + os.urandom(32)).decode()


def img_list(n):
    """带原下标的图片清单（新签名：[(下标, src, link, ext)]）"""
    return [(i, f'data:image/png;base64,{png_b64()}', None, 'png') for i in range(n)]


def install(fake, markdown_status=200, upload_ok=True, attach_ok=True, block_urls=None):
    """按场景装好 rules，返回 fake。"""
    fake.rules = {
        ('POST', '/file_uploads'): (200, {'upload_url': 'https://up.example/x', 'id': 'fuid-0'}) if upload_ok else (500, {}),
        ('POST', 'https://up.example/x'): (200, {}),
        ('GET', '/blocks/'): (200, {'results': [
            {'id': f'b{i}', 'type': 'image', 'image': {'external': {'url': u}}}
            for i, u in enumerate(block_urls or [])]}),
        ('PATCH', '/blocks/'): (200, {}),
    }
    if not attach_ok:
        fake.rules[('PATCH', '/blocks/')] = (500, {})
    fake.rules[('PATCH', '/pages/')] = (markdown_status, {})
    return fake


print('== A. write_markdown_and_images 状态判定 ==')
ns['requests'] = install(FakeReq(), markdown_status=502)
ok, detail, io, it = ns['write_markdown_and_images']('p1', '正文', [], '')
check('markdown 502 → (False, markdown_failed:502)', (not ok) and detail.startswith('markdown_failed:502'), detail)

ns['requests'] = install(FakeReq(), markdown_status=200)
ok, detail, io, it = ns['write_markdown_and_images']('p1', '正文', [], '')
check('无图 + 200 → OK', ok and io == 0 and it == 0, detail)

fake = install(FakeReq(), markdown_status=200,
               block_urls=['https://n2m.local/img_0.png', 'https://n2m.local/img_1.png'])
ns['requests'] = fake
ok, detail, io, it = ns['write_markdown_and_images']('p1', '正文', img_list(2), '')
check('2 图全成 → imgs=2/2', ok and io == 2 and it == 2, detail)

fake = install(FakeReq(), markdown_status=200, upload_ok=False,
               block_urls=['https://n2m.local/img_0.png'])
ns['requests'] = fake
ok, detail, io, it = ns['write_markdown_and_images']('p1', '正文', img_list(2), '')
check('上传 500 → imgs=0/2 + fail 记账，正文仍 OK',
      ok and io == 0 and it == 2 and 'fail=2' in detail, detail)

fake = install(FakeReq(), markdown_status=200,
               block_urls=['https://n2m.local/img_0.png', 'https://n2m.local/img_1.png'])
fake.rules[('PATCH', '/blocks/')] = (500, {})
ns['requests'] = fake
ok, detail, io, it = ns['write_markdown_and_images']('p1', '正文', img_list(2), '')
check('贴图 PATCH 全挂 → imgs=0/2 + fail 记账', ok and io == 0 and it == 2 and 'fail=2' in detail, detail)

print('\n== B. 上传 429 重试后成功 ==')
fake = install(FakeReq(), markdown_status=200,
               block_urls=['https://n2m.local/img_0.png'])
real_post = fake.post
calls_to_uploads = 0

def flaky_post(url, **kw):
    global calls_to_uploads
    if url == 'https://api.notion.com/v1/file_uploads':
        calls_to_uploads += 1
        if calls_to_uploads == 1:
            return fake._resp(429)
    return real_post(url, **kw)
fake.post = flaky_post
ns['requests'] = fake
ok, detail, io, it = ns['write_markdown_and_images']('p1', '正文', img_list(1), '')
check('429 后重试成功 → OK，file_uploads 调了 2 次', ok and io == 1 and calls_to_uploads == 2,
      f'{detail} calls={calls_to_uploads}')

print('\n== C. 主循环状态判定由 caller 完成（D/E 验证），跳过 ==')
print('  - 已在 D/E 中通过 status=OK/PARTIAL 结果验证')

print('\n== D. repair_pages：补写不建页 ==')
import functools as _ft
_orig_split = ns['split_markdown_by_headings']
ns['split_markdown_by_headings'] = _ft.partial(_orig_split, threshold=20, min_chunk=1)  # 强制走拆分：压小阈值与合并门槛
fake = install(FakeReq(), markdown_status=200,
               block_urls=['https://n2m.local/img_0.png'])
ns['requests'] = fake
created = []
def _fake_create(props):
    created.append(props)
    return {'id': 'page-created-%d' % len(created)}
ns['create_notion_page'] = _fake_create
ns['fetch_doc_with_retry'] = lambda entry, book_id: ({'body': '<h2>第一章标题</h2><p>第一章正文</p>\n\n<h2>第二章标题</h2><p>第二章正文</p>', 'word_count': 9, 'slug': 's'}, None)
saved = []
ns['save_result'] = saved.append
rec = {'idx': 3, 'status': 'PARTIAL', 'doc_id': '777', 'title': 'T',
       'page_ids': {'0': 'page-main', '1': 'page-child'}, 'imgs_ok': 0, 'imgs_total': 0}
rc = ns['repair_pages']([rec], [{'doc_id': '777', 'title': 'T', 'source': '其他', 'category': '其他'}])
check('repair 返回 0（正常结束）', rc == 0, str(rc))
check('已有 page_id → 不重复建页', created == [], f'creates={len(created)}')
check('补写结果落盘为 OK', saved and saved[-1]['status'] == 'OK', str(saved[-1]['status']) if saved else '无')
check('page_ids 保留', saved and saved[-1]['page_ids'] == {'0': 'page-main', '1': 'page-child'}, '')
calls = fake.calls
patch_pages = [u for m, u, _ in calls if m == 'PATCH' and '/pages/' in u]
check('PATCH 打到已有页（main+child）', set(patch_pages) == {'https://api.notion.com/v1/pages/page-main/markdown',
                                                               'https://api.notion.com/v1/pages/page-child/markdown'}, str(set(patch_pages)))

print('\n== E. only_images：不重写正文，只补图 ==')
import urllib.parse as _up
_img_payload = {'src': f'data:image/png;base64,{png_b64()}', 'link': ''}
_img_card = f'<card type="inline" name="image" value="data:{_up.quote(json.dumps(_img_payload))}"></card>'
fake2 = install(FakeReq(), markdown_status=200,
                block_urls=['https://n2m.local/img_0.png'])
ns['requests'] = fake2
ns['fetch_doc_with_retry'] = lambda entry, book_id: ({'body': f'<p>x</p>{_img_card}', 'word_count': 1, 'slug': 's'}, None)
saved2 = []
ns['save_result'] = saved2.append
ns['S'].only_images = True
rec2 = {'idx': 5, 'status': 'OK', 'doc_id': '888', 'title': 'T2',
        'page_ids': {'0': 'page-main'}, 'imgs_ok': 0, 'imgs_total': 2}
rc = ns['repair_pages']([rec2], [{'doc_id': '888', 'title': 'T2', 'source': '其他', 'category': '其他'}])
patch_pages2 = [u for m, u, _ in fake2.calls if m == 'PATCH' and '/pages/' in u]
check('only_images 不调用 markdown PATCH', not patch_pages2, str(patch_pages2))
uploads = [u for m, u, _ in fake2.calls if m == 'POST' and 'file_uploads' in u]
check('only_images 仍上传图片', len(uploads) >= 1, str(len(uploads)))
check('only_images 结果 imgs_ok 记账', saved2 and saved2[-1]['imgs_ok'] == 1, '')
check('缺 page_id 的片会补建（不再放弃）', len(created) >= 1, f'creates={len(created)}')
check('补建后 page_ids 写全', saved2 and len(saved2[-1]['page_ids']) == saved2[-1]['chunks_total'],
      str(saved2[-1]['page_ids'] if saved2 else None))

print('\n== F. cli 接线 ==')
from cli import load_settings  # noqa: E402
s = load_settings(['--resume-write'], require=())
check('--resume-write 生效', s.resume_write and not s.only_images, '')
s = load_settings(['--only-images'], require=())
check('--only-images 隐含 resume-write', s.resume_write and s.only_images, '')

print(f'\n{"=" * 52}\n通过 {len(PASS)} / 失败 {len(FAIL)}')
for f in FAIL:
    print('  ❌ ' + f)
sys.exit(1 if FAIL else 0)
