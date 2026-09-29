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

1. **Notion Integration Token**：环境变量 `NOTION_TOKEN`，或凭据文件 `~/.openclaw/secrets/credentials.env`
   - 该 Integration 必须已**共享给目标数据库**（Notion 里打开库页面 → 右上 `···` → 连接 → 添加）。没共享的话探活阶段就直接 `exit 2`
2. **语雀侧走的是语雀 Web API，认证靠 cookie + ctoken**（**不是** v2 Token）
   - 配在 `skills/yuque-ai-mcp/config/config.json` 的 `cookie` / `ctoken` 字段
   - 获取：浏览器登录 yuque.com → `F12` → Application → Cookies，复制 `_yuque_session`（→ `cookie`）和 `yuque_ctoken`（→ `ctoken`）
   - **过期症状**：抓取返回空 body → 日志刷 `[retry] body 空（疑似风控）` → 大批 `RATE_LIMITED`；重新复制 cookie 即可
   - 工具**没有 v2 Token 兜底**，cookie 失效就是全线停摆
3. **目标 Notion 数据库**字段齐备（见上表），且判重字段（默认 `原文链接`）是 `url` 类型
4. 外部命令 **`mcporter`**（含 yuque-mcp MCP server）在 PATH 里；否则用 `--mcp-cwd` 指向工作区
5. Python ≥ 3.8，依赖见 `requirements.txt`

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
| `--dedup-property` | `NOTION_DEDUP_PROPERTY` | 去重判重字段（默认 `原文链接`，必须是 url 类型） |
| `--book-id` / `--namespace` / `--user` | `YUQUE_BOOK_ID` / `YUQUE_NAMESPACE` / `YUQUE_USER` | 语雀知识库 |
| `--mode` | — | `new` / `resume`（默认）/ `retry-failed` |
| `--state-dir` | `YUQUE2NOTION_STATE_DIR` | 状态目录，默认 `<repo>/state/` |
| `--entries` | — | 条目文件路径，默认 `<state-dir>/yuque_entries.json` |
| `--env-file` | `OPENCLAW_CREDENTIALS` | 凭据文件，默认 `~/.openclaw/secrets/credentials.env` |
| `--mcp-cwd` | `OPENCLAW_WORKSPACE` | mcporter 工作目录 |
| `--batch-size` | `BATCH_SIZE` | 每批篇数 |
| — | `MAX_CONTENT_CHARS` / `SPLIT_MIN_LEVEL` / `SPLIT_MAX_LEVEL` / `SPLIT_MIN_CHUNK` | 分片阈值与标题层级 |
| `--dry-run` | — | 只探活 + 列批次，不写任何东西 |
| `--print-config` | — | 打印生效配置（token 打码）后退出 |
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

- 纯数字、单字符标签被过滤
- 专有名词（如 MyBatis-Plus）自动合并为 MyBatisPlus
- 标签上限 15 个
- 配置或目标库有问题时**直接报错退出**（`exit 2`），不会静默跑错库
- 状态结果：`OK`（完成）/ `PARTIAL`（页建了但正文没写全）/ `FAIL` / `RATE_LIMITED` / `SKIP`（垃圾或已迁）
- 单片超过 `MAX_CONTENT_CHARS`（默认 50000）会在**围栏外的行边界安全切分**（切在代码块里会闭合+重开围栏），不丢内容；只有「单行本身超长」这种无法再切的情况才截断，并记 `truncated:` 且该片判 `PARTIAL`

### 语雀卡片转换

| 卡片 | 转换结果 |
|---|---|
| `codeblock` | 围栏代码块（保留语言） |
| `image` | 原生上传到 Notion；base64 图走 file_upload |
| `math` | 含 LaTeX 特征（`\frac`/`^`/`_`…）→ `$…$` / `$$…$$`；否则当行内代码（语雀 math 卡片也会装普通代码） |
| `hr` | `---` |
| `file` / 附件 | `📎 [文件名](url)` |
| `bookmark` / 链接 | `[标题](url)` |
| `video` / `audio` | `[视频](url)` |
| 表格 / 画板 / 思维导图 / **未知类型** | 可见占位 `> ⚠️ 未转换的语雀卡片：xxx（请回原文查看）`，并在结果里记 `unsupported_cards` |

**任何卡片都不会被静默丢掉**：不认识的类型一律落成可见占位 + 记账。

## 长文档分片

正文转换后超过 `MAX_CONTENT_CHARS`（默认 50000 字）会自动拆成多页：

- 按 `##`~`######` 标题切分（`#` 视为文档标题不切，层级可用 `SPLIT_MIN_LEVEL` / `SPLIT_MAX_LEVEL` 调）
- **不切进代码围栏**（围栏内的 `##` 只是代码）；**首个标题之前的内容独立成第 0 片**（写入主页面）
- 第 0 片 → 主页面；第 N 片 → 独立页，标题为 `<文档名> - <章节名>`，备注注明拆分来源
- 分片页与主页面**共享同一个判重键**，所以去重是「数页数」：片齐才跳过，缺片会**自动补建**
- 每片只上传本片用到的图片（不重复上传全量图）

## 故障排查

### 直接 `exit 2` 的四种情况

| 输出 | 原因 | 处理 |
|---|---|---|
| `[FATAL] Notion 探活失败: 目标库不可访问 404` | 库 ID 错 / 库没共享给 Integration / 库已删 | 核对 `--db-id`；在 Notion 里把库连上 Integration |
| `[FATAL] 条目清单变了…就加 --reset-cursor` | 重跑过 `build_entries.py`，清单顺序变了 | 确认清单无误后加 `--reset-cursor` |
| `[FATAL] 本批 N 篇全部失败…疑似语雀风控` | cookie 过期 / 断网 | 重贴 cookie，稍后重跑（**游标已回滚到批首，不会漏篇**） |
| `[FATAL] 条目文件不存在` / `没有结果可补` | 没先跑 `build_entries.py` / 补写时结果文件不存在 | 按提示先生成条目 / 先跑一轮迁移 |

### 状态语义与谁来兜底

| 状态 | 含义 | 谁来兜 |
|---|---|---|
| `OK` | 全部片正文写完（图片可能缺，记 `imgs=x/y`） | — |
| `PARTIAL` | 页建了但正文没写全 | `--resume-write` |
| `FAIL` / `RATE_LIMITED` | 抓取或建页失败 | 自动进重试队列 → `--mode retry-failed` |
| `SKIP` | 垃圾文档（二进制/SQL dump/空）或 Notion 已存在 | — |

### 常备动作

```bash
# 看这次要用哪个库、哪个知识库，值来自哪一层
python3 scripts/batch_migrate.py --print-config

# 半成品补写（按记录里的 page_id，不重建页）
python3 scripts/batch_migrate.py --resume-write
python3 scripts/batch_migrate.py --resume-write --only-images   # 只补图

# 清重试队列
python3 scripts/batch_migrate.py --mode retry-failed
```

## 测试

184 项，**全离线**（假 `mcporter` + `sitecustomize` 拦掉网络与 sleep），不需要真库、不写任何数据：

```bash
python3 tests/test_settings.py      # 配置优先级 / fail-fast
python3 tests/test_write_status.py  # 正文写失败判定 / 图片记账 / 补写
python3 tests/test_cursor.py        # 游标推进 / 安全阀 / 每篇落盘 / 分桶 / 指纹
python3 tests/test_split.py         # 拆分器：围栏 / 引言 / 深度 / 零丢失
python3 tests/test_cards.py         # 卡片转换：math/hr/附件/未知类型
python3 tests/test_bigdoc.py        # 大文档：缺片补建 / 每片只传本片图
python3 tests/test_images.py        # 图片扩展名字节嗅探
```

每个脚本独立可跑，末尾输出 `通过 N / 失败 0`，失败时 exit 1。