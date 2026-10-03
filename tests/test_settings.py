#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置优先级离线单测（不联网、不写任何外部系统）

用法：python3 tests/test_settings.py
"""
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts'))

import config  # noqa: E402
from cli import load_settings, read_env_file, ConfigError, DEFAULT_STATE_DIR  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(('  ✅ ' if cond else '  ❌ ') + f'{name}' + (f' | {detail}' if detail else ''))


def clean_env():
    for k in ('NOTION_DATABASE_ID', 'NOTION_DATA_SOURCE_ID', 'YUQUE_BOOK_ID',
              'YUQUE_NAMESPACE', 'YUQUE_USER', 'NOTION_TOKEN', 'OPENCLAW_CREDENTIALS',
              'YUQUE2NOTION_STATE_DIR', 'NOTION_DEDUP_PROPERTY'):
        os.environ.pop(k, None)


def main():
    print('== 1. 优先级：命令行 > 环境变量 > config.py ==')
    clean_env()
    s = load_settings(['--db-id', 'cli-db'], require=())
    check('命令行生效', s.notion_db_id == 'cli-db' and s.sources['notion_db_id'] == '命令行',
          f"{s.notion_db_id} / {s.sources['notion_db_id']}")

    os.environ['NOTION_DATABASE_ID'] = 'env-db'
    s = load_settings([], require=())
    check('环境变量覆盖 config', s.notion_db_id == 'env-db' and '环境变量' in s.sources['notion_db_id'],
          f"{s.notion_db_id} / {s.sources['notion_db_id']}")

    s = load_settings(['--db-id', 'cli-db'], require=())
    check('命令行压过环境变量', s.notion_db_id == 'cli-db', s.notion_db_id)

    clean_env()
    s = load_settings([], require=())
    check('都没给 → 回落到 config.py', s.notion_db_id == config.NOTION_DATABASE_ID
          and s.sources['notion_db_id'] == 'config.py', s.sources['notion_db_id'])

    print('\n== 2. mode 解析（含旧式裸参数兼容） ==')
    s = load_settings(['new'], require=())
    check('裸 new 兼容', s.mode == 'new', s.mode)
    s = load_settings(['new', '--mode', 'retry-failed'], require=())
    check('--mode 压过裸参数', s.mode == 'retry-failed', s.mode)
    s = load_settings([], require=())
    check('默认 resume', s.mode == 'resume', s.mode)

    print('\n== 3. 状态目录派生 ==')
    s = load_settings(['--state-dir', '/tmp/y2n-test'], require=())
    check('entries 落在 state-dir', s.entries_file == '/tmp/y2n-test/yuque_entries.json', s.entries_file)
    check('progress 落在 state-dir', s.progress_file == '/tmp/y2n-test/progress.txt', s.progress_file)
    check('prev_results 落在 state-dir', s.prev_results_file == '/tmp/y2n-test/prev_results.json',
          s.prev_results_file)
    s = load_settings([], require=())
    check('默认 state-dir = <repo>/state', s.state_dir == DEFAULT_STATE_DIR, s.state_dir)

    print('\n== 4. fail-fast：必填缺失 / 凭据缺失 / 非法整数 ==')
    clean_env()
    old_db = config.NOTION_DATABASE_ID
    try:
        config.NOTION_DATABASE_ID = ''
        try:
            load_settings([], require=('notion',))
            check('缺 db_id → ConfigError', False, '居然没抛')
        except ConfigError as e:
            check('缺 db_id → ConfigError', True, str(e)[:40])
    finally:
        config.NOTION_DATABASE_ID = old_db

    old_book = config.YUQUE_BOOK_ID
    try:
        config.YUQUE_BOOK_ID = ''
        try:
            load_settings([], require=('book',))
            check('缺 book_id → ConfigError', False, '居然没抛')
        except ConfigError as e:
            check('缺 book_id → ConfigError', True, str(e)[:40])
    finally:
        config.YUQUE_BOOK_ID = old_book

    # token：环境变量和凭据文件都没有 → 必须报错
    os.environ['OPENCLAW_CREDENTIALS'] = '/tmp/__no_such_creds__.env'
    try:
        load_settings([], require=('notion',))
        check('缺 NOTION_TOKEN → ConfigError', False, '居然没抛')
    except ConfigError as e:
        check('缺 NOTION_TOKEN → ConfigError', True, str(e)[:40])
    finally:
        clean_env()

    old_bs = config.BATCH_SIZE
    try:
        config.BATCH_SIZE = 'abc'
        try:
            load_settings([], require=())
            check('非法 batch_size → ConfigError', False, '居然没抛')
        except ConfigError as e:
            check('非法 batch_size → ConfigError', True, str(e)[:40])
    finally:
        config.BATCH_SIZE = old_bs

    print('\n== 5. 凭据文件解析 ==')
    with tempfile.NamedTemporaryFile('w', suffix='.env', delete=False) as f:
        f.write('# 注释\n'
                'export NOTION_TOKEN="tok-123"\n'
                "GITHUB_PAT_CLASSIC='ghp_x'\n"
                '\n'
                'YUQUE_BOOK_ID=78624582\n')
        p = f.name
    try:
        creds = read_env_file(p)
        check('去引号 + 去 export', creds.get('NOTION_TOKEN') == 'tok-123', creds.get('NOTION_TOKEN'))
        check('单引号也能去', creds.get('GITHUB_PAT_CLASSIC') == 'ghp_x', creds.get('GITHUB_PAT_CLASSIC'))
        check('跳过注释空行', len(creds) == 3, str(len(creds)))
        check('文件不存在返回空 dict', read_env_file('/tmp/__nope__.env') == {}, 'ok')
    finally:
        os.unlink(p)

    print('\n== 6. 凭据文件里的 token 也能被读到 ==')
    with tempfile.NamedTemporaryFile('w', suffix='.env', delete=False) as f:
        f.write('export NOTION_TOKEN=tok-from-file\n')
        p = f.name
    try:
        clean_env()
        s = load_settings(['--env-file', p], require=('notion',))
        check('凭据文件兜底', s.notion_token == 'tok-from-file', s.notion_token)
    finally:
        os.unlink(p)
        clean_env()

    print('\n== 7. 真实跑一次 --print-config（不联网、不碰库） ==')
    import subprocess
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with tempfile.NamedTemporaryFile('w', suffix='.env', delete=False) as f:
        f.write('export NOTION_TOKEN=tok-print\n')
        p = f.name
    try:
        env = dict(os.environ)
        env.pop('NOTION_TOKEN', None)
        r = subprocess.run([sys.executable, os.path.join(repo, 'scripts', 'batch_migrate.py'),
                            '--print-config', '--env-file', p],
                           capture_output=True, text=True, cwd=repo, env=env)
        check('--print-config exit 0', r.returncode == 0, str(r.returncode))
        check('打印生效配置', '生效配置' in r.stdout, r.stdout.splitlines()[:1])
        check('打印来源层', '[命令行]' in r.stdout or '[config.py]' in r.stdout, 'ok')
        check('token 已打码', 'tok-print' not in r.stdout and 'tok-***rint' not in r.stdout.replace('tok-***', 'X'),
              'ok')
    finally:
        os.unlink(p)
        clean_env()

    print('\n== 8. 各脚本只列自己的开关（--help 不再误导） ==')
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env2 = dict(os.environ)
    env2['YUQUE2NOTION_STATE_DIR'] = tempfile.mkdtemp(prefix='y2n-help-')

    def help_flags(script):
        r = subprocess.run([sys.executable, os.path.join(repo, 'scripts', script), '--help'],
                           capture_output=True, text=True, cwd=repo, env=env2)
        return set(re.findall(r'(--[a-z][a-z0-9-]+)', r.stdout)), r.returncode

    bf, brc = help_flags('build_entries.py')
    check('build_entries 只列相关开关', brc == 0 and '--book-id' in bf and '--resume-write' not in bf
          and '--db-id' not in bf and '--mode' not in bf, f'rc={brc} flags={sorted(bf)}')
    lf, lrc = help_flags('lake_convert.py')
    check('lake_convert 只列相关开关', lrc == 0 and '--book-id' in lf and '--only-images' not in lf,
          f'rc={lrc} flags={sorted(lf)}')
    mf, mrc = help_flags('batch_migrate.py')
    check('batch_migrate 全量开关齐全', mrc == 0 and {'--db-id', '--resume-write', '--mode', '--dry-run'} <= mf,
          f'rc={mrc} n={len(mf)}')

    print(f'\n{"=" * 52}\n通过 {len(PASS)} / 失败 {len(FAIL)}')
    for f in FAIL:
        print('  ❌ ' + f)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
