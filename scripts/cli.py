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


def build_parser(prog='batch_migrate'):
    p = argparse.ArgumentParser(
        prog=prog,
        description='语雀 → Notion 迁移工具（优先级：命令行 > 环境变量 > config.py 默认值）')
    # 兼容旧用法：python3 batch_migrate.py new
    p.add_argument('legacy_mode', nargs='?', choices=['new', 'resume', 'retry-failed'],
                   default=None, help=argparse.SUPPRESS)
    p.add_argument('--mode', choices=['new', 'resume', 'retry-failed'],
                   help='运行模式（默认 resume）')
    p.add_argument('--db-id', dest='db_id', help='Notion 库 ID')
    p.add_argument('--ds-id', dest='ds_id', help='Notion data source ID（库下多源时必填）')
    p.add_argument('--dedup-property', dest='dedup_property', help='去重判重字段（url 类型）')
    p.add_argument('--book-id', dest='book_id', help='语雀知识库 ID')
    p.add_argument('--namespace', help='语雀知识库 slug（拼原文链接用）')
    p.add_argument('--user', help='语雀用户名（拼原文链接用）')
    p.add_argument('--batch-size', dest='batch_size', type=int, help='每批篇数')
    p.add_argument('--state-dir', dest='state_dir', help=f'状态目录（默认 {DEFAULT_STATE_DIR}）')
    p.add_argument('--entries', help='条目文件（默认 <state-dir>/yuque_entries.json）')
    p.add_argument('--env-file', dest='env_file',
                   help=f'凭据文件（默认 {DEFAULT_ENV_FILE}）')
    p.add_argument('--mcp-cwd', dest='mcp_cwd',
                   help=f'mcporter 工作目录（默认 {DEFAULT_MCP_CWD}）')
    p.add_argument('--dry-run', dest='dry_run', action='store_true',
                   help='只探活 + 列批次，不写任何东西')
    p.add_argument('--resume-write', dest='resume_write', action='store_true',
                   help='补写模式：只往上次结果里已存在的页补正文（PARTIAL 记录），不建页、不推进度')
    p.add_argument('--only-images', dest='only_images', action='store_true',
                   help='配合 --resume-write：只补图片，不重写正文（用于 imgs 缺失的记录）')
    p.add_argument('--reset-cursor', dest='reset_cursor', action='store_true',
                   help='把本任务的游标归零重扫（清单指纹变化时需显式指定）')
    p.add_argument('--print-config', dest='print_config', action='store_true',
                   help='打印生效配置（token 打码）后退出')
    return p


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
    args = build_parser(prog).parse_args(argv)
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

    def cfg_int(name, attr, default):
        v = _pick(name, None, None, getattr(C, attr, None), default, src)
        try:
            return int(v)
        except (TypeError, ValueError):
            raise ConfigError(f'{name} 不是合法整数：{v!r}（来自 {src[name]}）')

    batch_size = args.batch_size if args.batch_size is not None else cfg_int('batch_size', 'BATCH_SIZE', 10)
    if args.batch_size is not None:
        src['batch_size'] = '命令行'
    max_content_chars = cfg_int('max_content_chars', 'MAX_CONTENT_CHARS', 50000)
    split_min_level = cfg_int('split_min_level', 'SPLIT_MIN_LEVEL', 2)
    split_max_level = cfg_int('split_max_level', 'SPLIT_MAX_LEVEL', 6)
    split_min_chunk = cfg_int('split_min_chunk', 'SPLIT_MIN_CHUNK', 5120)
    dedup_query_retries = cfg_int('dedup_query_retries', 'DEDUP_QUERY_RETRIES', 3)
    dedup_max_consecutive_failures = cfg_int('dedup_max_consecutive_failures',
                                            'DEDUP_MAX_CONSECUTIVE_FAILURES', 2)

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
        print_config(s)
        sys.exit(0)
    return s


def print_config(s):
    src = s.sources
    rows = [
        ('mode', s.mode, 'mode'),
        ('notion_db_id', s.notion_db_id, 'notion_db_id'),
        ('notion_ds_id', s.notion_ds_id or '(自动解析)', 'notion_ds_id'),
        ('dedup_property', s.dedup_property, 'dedup_property'),
        ('yuque_book_id', s.book_id, 'book_id'),
        ('namespace', s.namespace, 'namespace'),
        ('user', s.user, 'user'),
        ('batch_size', s.batch_size, 'batch_size'),
        ('max_content_chars', s.max_content_chars, 'max_content_chars'),
        ('split_levels', f'{s.split_min_level}-{s.split_max_level} h级', 'split_min_level'),
        ('split_min_chunk', s.split_min_chunk, 'split_min_chunk'),
        ('state_dir', s.state_dir, 'state_dir'),
        ('entries_file', s.entries_file, 'entries_file'),
        ('progress_file', s.progress_file, 'progress_file'),
        ('prev_results_file', s.prev_results_file, 'prev_results_file'),
        ('env_file', s.env_file, 'env_file'),
        ('mcp_cwd', s.mcp_cwd, 'mcp_cwd'),
        ('dry_run', s.dry_run, 'dry_run'),
        ('resume_write', s.resume_write, 'resume_write'),
        ('only_images', s.only_images, 'only_images'),
        ('reset_cursor', s.reset_cursor, 'reset_cursor'),
        ('NOTION_TOKEN', _mask(s.notion_token), 'notion_token'),
    ]
    print('生效配置（命令行 > 环境变量 > config.py）')
    for k, v, skey in rows:
        print(f'  {k:24s} = {str(v):52s} [{src.get(skey, "-")}]')


def ensure_state_dir(s):
    try:
        os.makedirs(s.state_dir, exist_ok=True)
    except OSError as e:
        raise ConfigError(f'状态目录不可用 {s.state_dir}: {e}')
    return s.state_dir
