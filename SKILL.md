---
name: yuque-to-notion
description: 将语雀知识库中的文档批量迁移到 Notion 数据库，支持正文、标签、原文链接、图片与代码块的完整迁移（含大文档自动分片）。
---

# yuque-to-notion

将语雀知识库文档批量迁移到 Notion 数据库。正文转 Markdown 写入页面内容，元数据写入数据库字段。

## 前置条件

1. **Notion Integration Token**：环境变量 `NOTION_TOKEN`，或凭据文件
   `~/.openclaw/secrets/credentials.env`。
   该 Integration 必须已**共享给目标数据库**（Notion 里库页面 → ··· → 连接 → 添加）。
2. **语雀侧走的是语雀 Web API，认证靠 cookie + ctoken**（不是 v2 Token）：
   - 配在 yuque-mcp 的 `config/config.json`（`cookie` / `ctoken` 字段）
   - 获取：浏览器登录 yuque.com → F12 → Application → Cookies，复制
     `_yuque_session`（→ `cookie`）与 `yuque_ctoken`（→ `ctoken`）
   - 过期症状：抓取返回空 body，日志刷 `body 空（疑似风控）`，大批 `RATE_LIMITED`
   - **没有 v2 兜底**，cookie 失效即全线停摆
3. **目标 Notion 数据库**字段齐备，且判重字段是 `url` 类型
4. 外部命令 `mcporter`（含 yuque-mcp）在 PATH 里；否则用 `--mcp-cwd` 指工作区

## 字段映射

| 语雀字段 | Notion 字段 | 类型 |
|---|---|---|
| 标题 | 名称 | Title |
| 来源 | 来源 | Select |
| 分类 | 分类 | Select |
| 正文（转 Markdown） | 页面内容 | 页面正文 |
| 字数 | 字数 | Number |
| 标签 | 标签 | Multi-select |
| 语雀链接 | 原文链接 | URL |
| 原文出处 | 备注 | Rich Text |

## 使用方式

```bash
# 第一步：拉知识库目录 → 生成条目清单
python3 scripts/build_entries.py --book-id 78624582

# 看当前实际生效配置（哪个库、来自哪一层）
python3 scripts/batch_migrate.py --print-config

# 先探活 + 列批次，不写任何东西
python3 scripts/batch_migrate.py --dry-run

# 正式跑（每批 10 篇）
python3 scripts/batch_migrate.py
```

配置优先级：**命令行 > 环境变量 > config.py 默认值**，换库/换知识库不用改代码。

## 配置说明

`scripts/config.py` 只放默认值：

- `NOTION_DATABASE_ID` / `NOTION_DATA_SOURCE_ID` / `NOTION_DEDUP_PROPERTY`
- `YUQUE_BOOK_ID` / `YUQUE_NAMESPACE` / `YUQUE_USER`
- `BATCH_SIZE` / `MAX_CONTENT_CHARS` / `SPLIT_MIN_LEVEL` / `SPLIT_MAX_LEVEL` / `SPLIT_MIN_CHUNK`
- `STATE_DIR` / `ENV_FILE` / `MCP_CWD`（留空用内置默认）

以上都可用环境变量（同名大写）覆盖，运行时参数用命令行开关（见 `--help`）。

## 迁移流程

1. `build_entries.py` 拉知识库目录 → 生成 `state/yuque_entries.json`
2. 逐篇取正文（语雀 Web API）→ lake 转 Markdown
3. 正文预处理：卡片分类转换（代码块/图片/公式/附件…）、HTML→Markdown、图片按需上传
4. 长文档按标题分片（超 `MAX_CONTENT_CHARS` 自动拆页）
5. 建 Notion 页面（元数据 + 正文），写结果与游标
6. 失败进重试队列（`--mode retry-failed`）、半成品可 `--resume-write` 补写

## 状态文件（`state/`，可用 `--state-dir` 改）

| 文件 | 作用 |
|---|---|
| `yuque_entries.json` | 条目清单（含指纹校验用的 doc_id/title） |
| `progress.json` | 游标，按 `book_id:notion_db_id` 分桶 |
| `prev_results.json` | 每篇的账（doc_id 维度合并） |
| `retry_queue.json` | 重试队列 |

## 完整文档

用法、环境变量表、状态语义、卡片转换表、故障排查、测试跑法 → 见 `README.md`。
