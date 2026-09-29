# ─────────────────────────────────────────────────────────────
# 默认值。真正生效的值 = 命令行 > 环境变量 > 这里。
# 想看当前生效的是什么、来自哪一层：python3 scripts/batch_migrate.py --print-config
# ─────────────────────────────────────────────────────────────

# Notion 数据库配置
NOTION_DATABASE_ID = "3c1d7ec2-3709-80f7-8040-ffbfe5e0c2f2"
NOTION_DATA_SOURCE_ID = ""   # 留空=从库自动解析；填了=校验后强制使用

# 语雀配置
YUQUE_BOOK_ID = "78624582"   # 废弃16
YUQUE_NAMESPACE = "twpgac"   # 知识库 slug（拼原文链接用）
YUQUE_USER = "yehuoshun"

# 去重配置（Notion 侧判重）
NOTION_DEDUP_PROPERTY = "原文链接"   # 判重字段，必须是 url 类型
DEDUP_QUERY_RETRIES = 3              # 429/5xx 重试次数
DEDUP_MAX_CONSECUTIVE_FAILURES = 2   # 去重查询连续失败这么多篇就整跑停（fail-fast）

# 迁移配置
BATCH_SIZE = 10
MAX_CONTENT_CHARS = 50000     # 单片最大字符数（唯一来源，不要在别处再写一遍）

# 拆分（长文档按标题切分）
SPLIT_MIN_LEVEL = 2           # 从 h2 开始拆（h1 视为文档标题，不拆）
SPLIT_MAX_LEVEL = 6           # 拆到 h6
SPLIT_MIN_CHUNK = 5120        # 章节小于此并入上一节（第 0 片例外，永远独立）

# 路径（留空 = 用内置默认，见 cli.py）
STATE_DIR = ""    # 默认 <repo>/state
ENV_FILE = ""     # 默认 ~/.openclaw/secrets/credentials.env
MCP_CWD = ""      # 默认 ~/.openclaw/workspace（mcporter 靠 cwd 找 MCP 配置）
