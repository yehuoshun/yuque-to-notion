# Notion 数据库配置
NOTION_DATABASE_ID = "3c1d7ec2-3709-80f7-8040-ffbfe5e0c2f2"
NOTION_DATA_SOURCE_ID = ""   # 留空=从库自动解析；填了=校验后强制使用

# 语雀配置
YUQUE_BOOK_ID = "78624582"  # 废弃16
YUQUE_NAMESPACE = "twpgac"    # 知识库 slug
YUQUE_USER = "yehuoshun"

# 去重配置（Notion 侧判重）
NOTION_DEDUP_PROPERTY = "原文链接"   # 判重字段，必须是 url 类型
DEDUP_QUERY_RETRIES = 3              # 429/5xx 重试次数
DEDUP_MAX_CONSECUTIVE_FAILURES = 2   # 去重查询连续失败这么多篇就整跑停（fail-fast）

# 迁移配置
BATCH_SIZE = 10
MAX_CONTENT_CHARS = 50000     # 正文最大长度
SLEEP_INTERVAL = 0.3          # API 调用间隔（秒）