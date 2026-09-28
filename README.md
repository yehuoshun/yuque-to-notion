# Yuque to Notion 迁移工具

将语雀知识库文档迁移到 Notion 数据库，保留正文内容、标签、原文链接等信息。

## 功能

- 从语雀知识库批量读取文档（含正文、字数、slug）
- 自动分类：AI/大模型、编程技术、操作系统、日记/随笔 等
- 自动提取标签：从标题、来源、分类、正文技术术语生成
- 自动提取原文链接：从正文中识别博客园/LINUX DO/B站等原始出处
- 正文转 Markdown 写入 Notion 页面
- 分批迁移，每批 10 条，方便审查

## 字段映射

| Notion 字段 | 类型 | 说明 |
|------------|------|------|
| 名称 | Title | 文档标题 |
| 来源 | Select | 博客园 / LINUX DO / Bangumi / B站 / 小黑盒 / 原创 / 其他 |
| 分类 | Select | AI/大模型 / 编程技术 / 操作系统 / 安全 / 影视/动漫 / 日记/随笔 / 工作/求职 / 投资/财经 / 读书 / 游戏 / 音乐制作 / 福利/羊毛 / 其他 |
| 原文链接 | URL | 语雀文档链接 `https://www.yuque.com/{user}/{repo}/{slug}` |
| 字数 | Number | 语雀 word_count |
| 标签 | Multi-select | 自动提取的关键词（含技术术语、专有名词） |
| 备注 | Rich Text | 原文出处（如有） |

## 使用方式

### 前置条件

1. Notion Integration Token 已配置（环境变量 `NOTION_TOKEN`，或 `~/.openclaw/secrets/credentials.env`）
2. 语雀 MCP（`yuque-mcp`）已配置——本工具走语雀 Web API，需要 cookie + ctoken
3. 目标 Notion 数据库已创建好字段，且已把该库共享给上面那个 Integration

### 运行

```bash
# 第一步：拉知识库目录 → 生成条目文件（写到 <state-dir>/yuque_entries.json）
python3 scripts/build_entries.py --book-id 78624582

# 第二步：分批迁移到 Notion（每批 10 条）
python3 scripts/batch_migrate.py
```

### 配置：命令行 > 环境变量 > config.py

```bash
export NOTION_DATABASE_ID=...     # 或 --db-id

python3 scripts/batch_migrate.py --help          # 全部参数
python3 scripts/batch_migrate.py --print-config  # 看当前实际生效值 + 来自哪一层
```

| 参数 | 环境变量 | 说明 |
|---|---|---|
| `--db-id` / `--ds-id` | `NOTION_DATABASE_ID` / `NOTION_DATA_SOURCE_ID` | Notion 库 / data source |
| `--book-id` / `--namespace` / `--user` | `YUQUE_BOOK_ID` / `YUQUE_NAMESPACE` / `YUQUE_USER` | 语雀知识库 |
| `--state-dir` | `YUQUE2NOTION_STATE_DIR` | 状态目录，默认 `<repo>/state/` |
| `--env-file` | `OPENCLAW_CREDENTIALS` | 凭据文件，默认 `~/.openclaw/secrets/credentials.env` |
| `--dry-run` | — | 只探活 + 列批次，不写任何东西 |
| `--reset-cursor` | — | 游标归零重扫（清单指纹变化时用） |
| `--resume-write` / `--only-images` | — | 补写模式：按记录里的 page_id 补正文 / 只补图 |

常用例子：

```bash
# 换一张库、换一个知识库，不改代码
python3 scripts/batch_migrate.py --db-id <库ID> --book-id <知识库ID> --state-dir ./state

# 先看这批要处理哪几篇，确认再跑
python3 scripts/batch_migrate.py --dry-run
```

### 迁移模式

| 模式 | 说明 |
|---|---|
| `resume`（默认） | 从游标继续；已完成/已在 Notion 的自动跳过 |
| `new` | 重置进度、结果与重试队列，从头开始 |
| `retry-failed` | 消费重试队列（按 `--batch-size` 分批） |

```bash
python3 scripts/batch_migrate.py --mode retry-failed
```

### 状态文件

默认落在 `<repo>/state/`（可用 `--state-dir` 改）：

| 文件 | 作用 |
|---|---|
| `progress.json` | 游标，按 `book_id:notion_db_id` 分桶，带清单指纹与上批计数 |
| `prev_results.json` | 每篇的账，按 doc_id 合并（状态取优先级最高） |
| `retry_queue.json` | 重试队列（`--mode retry-failed` 逐篇消费） |
| `yuque_entries.json` | `build_entries.py` 生成的条目清单 |
| `progress.txt` | 旧格式镜像（只写数字，兼容/便于人看） |

每篇处理完立刻落盘 → 进程崩溃或机器重启最多重来 1 篇。

游标只增不减；整批全部失败（无 OK/PARTIAL/SKIP）时会回滚到批首并 `exit 2`，提示疑似风控/网络故障。
清单指纹不一致（重跑了 `build_entries.py` 导致顺序变化）会拒绝沿用旧游标，需显式 `--reset-cursor`。

## 注意事项

- 单个 chunk 超过 `MAX_CONTENT_CHARS`（默认 50000）会被截断
- 纯数字、单字符标签被过滤
- 专有名词（如 MyBatis-Plus）自动合并为 MyBatisPlus
- 标签上限 15 个
- 配置或目标库有问题时**直接报错退出**（`exit 2`），不会静默跑错库
- 状态结果：`OK`（完成）/ `PARTIAL`（页建了但正文没写全）/ `FAIL` / `RATE_LIMITED` / `SKIP`（垃圾或已迁）