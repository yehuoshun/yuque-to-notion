#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一配置解析：命令行 > 环境变量 > config.py 默认值。

三个脚本（batch_migrate / build_entries / lake_convert）都从这里拿配置，
不允许各自再读一遍 ——「现在用的是哪个库、哪个知识库」只有这一处能回答。
"""
import argparse
import os
import sys
from dataclasses import dataclass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)

DEFAULT_STATE_DIR = os.path.join(REPO_ROOT, 'state')
DEFAULT_ENV_FILE = os.path.expanduser('~/.openclaw/secrets/credentials.env')
DEFAULT_MCP_CWD = os.path.expanduser('~/.openclaw/workspace')


class ConfigError(RuntimeError):
    """配置缺失/不可用。一律 fail-fast，不静默降级。"""


@dataclass
class Settings:
    mode: str
    notion_db_id: str
    notion_ds_id: str
    dedup_property: str
    book_id: str
    namespace: str
    user: str
    batch_size: int
    max_content_chars: int
    split_min_level: int
    split_max_level: int
    split_min_chunk: int
    dedup_query_retries: int
    dedup_max_consecutive_failures: int
    state_dir: str
    entries_file: str
    progress_file: str
    prev_results_file: str
    env_file: str
    mcp_cwd: str
    dry_run: bool
    resume_write: bool
    only_images: bool
    reset_cursor: bool
    notion_token: str
    sources: dict  # 字段 -> 生效来源，供 --print-config 用


# 每个脚本真正用得上的开关（不再一股脑全列出来，免得 --help 误导）
_SCRIPT_FLAGS = {
    'batch_migrate': ('mode', 'db_id', 'ds_id', 'dedup_property', 'book_id', 'namespace', 'user',
                      'batch_size', 'state_dir', 'entries', 'env_file', 'mcp_cwd', 'dry_run',
                      'resume_write', 'only_images', 'reset_cursor', 'print_config'),
    'build_entries': ('book_id', 'namespace', 'user', 'state_dir', 'entries',
                      'env_file', 'mcp_cwd', 'print_config'),
    'lake_convert': ('book_id', 'namespace', 'user', 'state_dir', 'entries',
                     'env_file', 'mcp_cwd', 'print_config'),
}


_SCRIPT_DESC = {
    'batch_migrate': '语雀 → Notion 迁移（优先级：命令行 > 环境变量 > config.py 默认值）',
    'build_entries': '拉语雀知识库目录 → 生成条目清单（供 batch_migrate 用）',
    'lake_convert': '语雀 lake 文档 → Markdown 转换（单篇调试用）',
}


def build_parser(prog='batch_migrate'):
    allowed = _SCRIPT_FLAGS.get(prog, _SCRIPT_FLAGS['batch_migrate'])
    on = lambda *names: any(n in allowed for n in names)   # noqa: E731

    p = argparse.ArgumentParser(prog=prog, description=_SCRIPT_DESC.get(prog, _SCRIPT_DESC['batch_migrate']))
    # 兼容旧用法：python3 batch_migrate.py new
    if 'mode' in allowed:
        p.add_argument('legacy_mode', nargs='?', choices=['new', 'resume', 'retry-failed'],
                       default=None, help=argparse.SUPPRESS)
        p.add_argument('--mode', choices=['new', 'resume', 'retry-failed'],
                       help='运行模式（默认 resume）')
    if on('db_id'):
        p.add_argument('--db-id', dest='db_id', help='Notion 库 ID')
    if on('ds_id'):
        p.add_argument('--ds-id', dest='ds_id', help='Notion data source ID（库下多源时必填）')
    if on('dedup_property'):
        p.add_argument('--dedup-property', dest='dedup_property', help='去重判重字段（url 类型）')
    if on('book_id'):
        p.add_argument('--book-id', dest='book_id', help='语雀知识库 ID')
    if on('namespace'):
        p.add_argument('--namespace', help='语雀知识库 slug（拼原文链接用）')
    if on('user'):
        p.add_argument('--user', help='语雀用户名（拼原文链接用）')
    if on('batch_size'):
        p.add_argument('--batch-size', dest='batch_size', type=int, help='每批篇数')
    if on('state_dir'):
        p.add_argument('--state-dir', dest='state_dir', help=f'状态目录（默认 {DEFAULT_STATE_DIR}）')
    if on('entries'):
        p.add_argument('--entries', help='条目文件（默认 <state-dir>/yuque_entries.json）')
    if on('env_file'):
        p.add_argument('--env-file', dest='env_file', help=f'凭据文件（默认 {DEFAULT_ENV_FILE}）')
    if on('mcp_cwd'):
        p.add_argument('--mcp-cwd', dest='mcp_cwd',
                       help=f'mcporter 工作目录（默认 {DEFAULT_MCP_CWD}）')
    if on('dry_run'):
        p.add_argument('--dry-run', dest='dry_run', action='store_true',
                       help='只探活 + 列批次，不写任何东西')
    if on('resume_write'):
        p.add_argument('--resume-write', dest='resume_write', action='store_true',
                       help='补写模式：只往上次结果里已存在的页补正文（PARTIAL 记录），不建页、不推进度')
    if on('only_images'):
        p.add_argument('--only-images', dest='only_images', action='store_true',
                       help='配合 --resume-write：只补图片，不重写正文（用于 imgs 缺失的记录）')
    if on('reset_cursor'):
        p.add_argument('--reset-cursor', dest='reset_cursor', action='store_true',
                       help='把本任务的游标归零重扫（清单指纹变化时需显式指定）')
    if on('print_config'):
        p.add_argument('--print-config', dest='print_config', action='store_true',
                       help='打印生效配置（token 打码）后退出')
    return p


# parse_args 后把本脚本没定义的开关补上默认值（load_settings 统一按全量字段读）
_ARG_DEFAULTS = {
    'legacy_mode': None, 'mode': None, 'db_id': None, 'ds_id': None, 'dedup_property': None,
    'book_id': None, 'namespace': None, 'user': None, 'batch_size': None, 'state_dir': None,
    'entries': None, 'env_file': None, 'mcp_cwd': None, 'dry_run': False, 'resume_write': False,
    'only_images': False, 'reset_cursor': False, 'print_config': False,
}


def _fill_defaults(args):
    for k, v in _ARG_DEFAULTS.items():
        if not hasattr(args, k):
            setattr(args, k, v)
    return args


def read_env_file(path):
    """读 KEY=VALUE 形式的凭据文件。文件不存在返回 {}（不抛错）。"""
    out = {}
    if not path or not os.path.exists(path):
        return out
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                k = k.replace('export ', '', 1).strip()
                out[k] = v.strip().strip('"').strip("'")
    except OSError:
        return {}
    return out


def _norm(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _pick(name, cli_val, env_key, cfg_val, default, sources):
    """按 命令行 > 环境变量 > config.py > 内置默认 取值，并记录来源。"""
    v = _norm(cli_val)
    if v is not None:
        sources[name] = '命令行'
        return v
    if env_key:
        v = _norm(os.environ.get(env_key))
        if v is not None:
            sources[name] = f'环境变量 {env_key}'
            return v
    v = _norm(cfg_val)
    if v is not None:
        sources[name] = 'config.py'
        return v
    sources[name] = '内置默认'
    return default


def _mask(secret):
    if not secret:
        return '(空)'
    if len(secret) <= 8:
        return '***'
    return f'{secret[:4]}***{secret[-4:]}'


def load_settings(argv=None, prog='batch_migrate', require=('notion', 'book')):
    """解析并校验配置。argv=None 时取 sys.argv[1:]。"""
    args = _fill_defaults(build_parser(prog).parse_args(argv))
    import config as C  # 只提供默认值，不含逻辑

    src = {}
    mode = _pick('mode', args.mode or args.legacy_mode, None, getattr(C, 'MODE', None), 'resume', src)
    db_id = _pick('notion_db_id', args.db_id, 'NOTION_DATABASE_ID', C.NOTION_DATABASE_ID, None, src)
    ds_id = _pick('notion_ds_id', args.ds_id, 'NOTION_DATA_SOURCE_ID', C.NOTION_DATA_SOURCE_ID, '', src)
    dedup_property = _pick('dedup_property', args.dedup_property, 'NOTION_DEDUP_PROPERTY',
                           getattr(C, 'NOTION_DEDUP_PROPERTY', None), '原文链接', src)
    book_id = _pick('book_id', args.book_id, 'YUQUE_BOOK_ID', C.YUQUE_BOOK_ID, None, src)
    namespace = _pick('namespace', args.namespace, 'YUQUE_NAMESPACE', C.YUQUE_NAMESPACE, '', src)
    user = _pick('user', args.user, 'YUQUE_USER', C.YUQUE_USER, '', src)
    state_dir = os.path.expanduser(_pick('state_dir', args.state_dir, 'YUQUE2NOTION_STATE_DIR',
                                         getattr(C, 'STATE_DIR', None), DEFAULT_STATE_DIR, src))
    env_file = os.path.expanduser(_pick('env_file', args.env_file, 'OPENCLAW_CREDENTIALS',
                                        getattr(C, 'ENV_FILE', None), DEFAULT_ENV_FILE, src))
    mcp_cwd = os.path.expanduser(_pick('mcp_cwd', args.mcp_cwd, 'OPENCLAW_WORKSPACE',
                                       getattr(C, 'MCP_CWD', None), DEFAULT_MCP_CWD, src))

    def cfg_int(name, attr, default, env_key=None):
        v = _pick(name, None, env_key, getattr(C, attr, None), default, src)
        try:
            return int(v)
        except (TypeError, ValueError):
            raise ConfigError(f'{name} 不是合法整数：{v!r}（来自 {src[name]}）')

    if args.batch_size is not None:
        batch_size = args.batch_size
        src['batch_size'] = '命令行'
    else:
        batch_size = cfg_int('batch_size', 'BATCH_SIZE', 10, 'BATCH_SIZE')
    max_content_chars = cfg_int('max_content_chars', 'MAX_CONTENT_CHARS', 50000, 'MAX_CONTENT_CHARS')
    split_min_level = cfg_int('split_min_level', 'SPLIT_MIN_LEVEL', 2, 'SPLIT_MIN_LEVEL')
    split_max_level = cfg_int('split_max_level', 'SPLIT_MAX_LEVEL', 6, 'SPLIT_MAX_LEVEL')
    split_min_chunk = cfg_int('split_min_chunk', 'SPLIT_MIN_CHUNK', 5120, 'SPLIT_MIN_CHUNK')
    dedup_query_retries = cfg_int('dedup_query_retries', 'DEDUP_QUERY_RETRIES', 3, 'DEDUP_QUERY_RETRIES')
    dedup_max_consecutive_failures = cfg_int('dedup_max_consecutive_failures',
                                            'DEDUP_MAX_CONSECUTIVE_FAILURES', 2,
                                            'DEDUP_MAX_CONSECUTIVE_FAILURES')

    entries_file = args.entries or os.path.join(state_dir, 'yuque_entries.json')
    src['entries_file'] = '命令行' if args.entries else 'state-dir 派生'
    src['progress_file'] = src['prev_results_file'] = 'state-dir 派生'
    src['notion_ds_id'] = src['notion_ds_id']
    src['notion_token'] = '环境变量 NOTION_TOKEN' if os.environ.get('NOTION_TOKEN') \
        else f'凭据文件 {os.path.basename(env_file)}'
    src.setdefault('dry_run', '命令行' if args.dry_run else '内置默认')

    # ── 必填校验（fail-fast）──
    if 'notion' in require and not db_id:
        raise ConfigError('缺少 Notion 库 ID：用 --db-id，或 export NOTION_DATABASE_ID=...，'
                          '或写进 config.py 的 NOTION_DATABASE_ID')
    if 'book' in require and not book_id:
        raise ConfigError('缺少语雀知识库 ID：用 --book-id，或 export YUQUE_BOOK_ID=...，'
                          '或写进 config.py 的 YUQUE_BOOK_ID')

    # ── 凭据：OS 环境变量 > 凭据文件 ──
    creds = read_env_file(env_file)
    notion_token = ''
    if 'notion' in require:
        notion_token = _norm(os.environ.get('NOTION_TOKEN')) or _norm(creds.get('NOTION_TOKEN')) or ''
        if not notion_token:
            raise ConfigError(f'NOTION_TOKEN 未配置：检查环境变量或凭据文件 {env_file}')

    s = Settings(
        mode=mode, notion_db_id=db_id or '', notion_ds_id=ds_id or '',
        dedup_property=dedup_property, book_id=book_id or '', namespace=namespace,
        user=user, batch_size=batch_size, max_content_chars=max_content_chars,
        split_min_level=split_min_level, split_max_level=split_max_level,
        split_min_chunk=split_min_chunk,
        dedup_query_retries=dedup_query_retries,
        dedup_max_consecutive_failures=dedup_max_consecutive_failures,
        state_dir=state_dir, entries_file=entries_file,
        progress_file=os.path.join(state_dir, 'progress.txt'),
        prev_results_file=os.path.join(state_dir, 'prev_results.json'),
        env_file=env_file, mcp_cwd=mcp_cwd, dry_run=bool(args.dry_run),
        resume_write=bool(args.resume_write) or bool(args.only_images),
        only_images=bool(args.only_images),
        reset_cursor=bool(args.reset_cursor),
        notion_token=notion_token, sources=src)

    if args.print_config:
        print_config(s, prog)
        sys.exit(0)
    return s


def print_config(s, prog='batch_migrate'):
    src = s.sources
    rows = [
        ('mode', s.mode, 'mode', 'run'),
        ('notion_db_id', s.notion_db_id, 'notion_db_id', 'notion'),
        ('notion_ds_id', s.notion_ds_id or '(自动解析)', 'notion_ds_id', 'notion'),
        ('dedup_property', s.dedup_property, 'dedup_property', 'notion'),
        ('yuque_book_id', s.book_id, 'book_id', 'book'),
        ('namespace', s.namespace, 'namespace', 'book'),
        ('user', s.user, 'user', 'book'),
        ('batch_size', s.batch_size, 'batch_size', 'run'),
        ('max_content_chars', s.max_content_chars, 'max_content_chars', 'run'),
        ('split_levels', f'{s.split_min_level}-{s.split_max_level} h级', 'split_min_level', 'run'),
        ('split_min_chunk', s.split_min_chunk, 'split_min_chunk', 'run'),
        ('state_dir', s.state_dir, 'state_dir', 'path'),
        ('entries_file', s.entries_file, 'entries_file', 'path'),
        ('progress_file', s.progress_file, 'progress_file', 'cursor'),
        ('prev_results_file', s.prev_results_file, 'prev_results_file', 'cursor'),
        ('env_file', s.env_file, 'env_file', 'path'),
        ('mcp_cwd', s.mcp_cwd, 'mcp_cwd', 'path'),
        ('dry_run', s.dry_run, 'dry_run', 'run'),
        ('resume_write', s.resume_write, 'resume_write', 'run'),
        ('only_images', s.only_images, 'only_images', 'run'),
        ('reset_cursor', s.reset_cursor, 'reset_cursor', 'run'),
        ('NOTION_TOKEN', _mask(s.notion_token), 'notion_token', 'token'),
    ]
    allowed = _SCRIPT_FLAGS.get(prog, _SCRIPT_FLAGS['batch_migrate'])
    # 每类配置保留条件：本脚本用得到的才打（path 类全保留；token 只在需要 Notion 时打）
    def keep(group):
        if group == 'path':
            return True
        if group == 'notion' or group == 'token':
            return 'db_id' in allowed
        if group == 'book':
            return 'book_id' in allowed
        # run / cursor：只有跑批的脚本才用得到
        return 'mode' in allowed

    print('生效配置（命令行 > 环境变量 > config.py）')
    for k, v, skey, group in rows:
        if not keep(group):
            continue
        print(f'  {k:24s} = {str(v):52s} [{src.get(skey, "-")}]')


def ensure_state_dir(s):
    try:
        os.makedirs(s.state_dir, exist_ok=True)
    except OSError as e:
        raise ConfigError(f'状态目录不可用 {s.state_dir}: {e}')
    return s.state_dir
