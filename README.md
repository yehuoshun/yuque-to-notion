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

1. 语雀 API Token 已配置
2. Notion Integration Token 已配置
3. 目标 Notion 数据库已创建并配有上述字段

### 运行

```bash
# 第一步：获取语雀知识库文档列表
python3 scripts/fetch_docs.py <book_id>

# 第二步：分批迁移到 Notion
python3 scripts/batch_migrate.py
```

### 批量迁移

每批处理 10 条，完成后等待确认。进度保存在 `batch_progress.txt`。

```bash
# 重置进度重新开始
rm -f batch_progress.txt
python3 scripts/batch_migrate.py
```

## 注意事项

- 正文超过 50000 字符会被截断
- 纯数字、单字符标签被过滤
- 专有名词（如 MyBatis-Plus）自动合并为 MyBatisPlus
- 标签上限 15 个