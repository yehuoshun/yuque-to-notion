---
name: yuque-to-notion
description: 将语雀知识库中的文档批量迁移到 Notion 数据库，支持正文内容、标签、原文链接的完整迁移。
---

# yuque-to-notion

将语雀知识库中的文档批量迁移到 Notion 数据库，支持正文内容、标签、原文链接的完整迁移。

## 前置条件

- Notion Integration Token 已配置（环境变量 `NOTION_TOKEN`）
- 语雀 Token 已配置
- Notion 数据库中已创建好目标字段

## 字段映射

| 语雀字段 | Notion 字段 | 类型 | 说明 |
|---------|------------|------|------|
| 标题 | 名称 | Title | 文档标题 |
| 来源 | 来源 | Select | 博客园 / LINUX DO / Bangumi 等 |
| 分类 | 分类 | Select | AI/大模型 / 编程技术 / 操作系统等 |
| 正文 | 正文内容 | Markdown | 正文转 Markdown 写入页面 |
| 字数 | 字数 | Number | 语雀 word_count |
| 标签 | 标签 | Multi-select | 从标题+正文自动提取关键词 |
| 语雀链接 | 原文链接 | URL | 语雀文档链接 |
| 原文出处 | 备注 | Rich Text | 原始出处链接（如有） |

## 使用方式

```bash
# 配置走 命令行 > 环境变量 > config.py，改库/改知识库不用改代码
python3 scripts/batch_migrate.py --help

# 看当前实际生效配置（哪个库、来自哪一层）
python3 scripts/batch_migrate.py --print-config

# 先探活 + 列批次，不写任何东西
python3 scripts/batch_migrate.py --dry-run

# 正式跑
python3 scripts/batch_migrate.py
```

状态文件默认落在 `<repo>/state/`（`yuque_entries.json` / `progress.txt` / `prev_results.json`），重启不丢。

## 配置说明

优先级：**命令行 > 环境变量 > config.py 默认值**。

config.py 只放默认值：
- `NOTION_DATABASE_ID` / `NOTION_DATA_SOURCE_ID`
- `YUQUE_BOOK_ID` / `YUQUE_NAMESPACE` / `YUQUE_USER`
- `NOTION_DEDUP_PROPERTY`（判重字段）
- `BATCH_SIZE` / `MAX_CONTENT_CHARS`
- `STATE_DIR` / `ENV_FILE` / `MCP_CWD`（留空用内置默认）

## 迁移流程

1. `build_entries.py` 拉知识库目录 → 生成条目文件
2. 获取语雀文档列表
2. 分类与标签提取
3. 创建 Notion 页面（元数据 + 正文内容）
4. 原文链接提取（从正文中识别原始出处）

## 配置说明

编辑 `scripts/config.py` 设置：
- NOTION_DATABASE_ID / NOTION_DATA_SOURCE_ID
- YUQUE_BOOK_ID / YUQUE_NAMESPACE / YUQUE_USER
- BATCH_SIZE / SLEEP_INTERVAL