# MikanDown 新功能设计文档

> 日期: 2026-05-15
> 方案: A（体验打磨）— 低投入，高感知价值，适合开源入门

## 一、配置向导

### 目标
让新用户在首次访问时能快速完成基础配置，无需探索侧边栏。

### 交互流程

```
首次访问（未检测到密码配置）→ 重定向到向导页面

向导步骤:
  Step 1/3: 设置管理员密码
  Step 2/3: 配置 qBittorrent 连接
  Step 3/3: 添加第一个 RSS 订阅

完成后 → 跳转到主界面
```

### 实现细节

- **前端**: 新建 `templates/wizard.html`，使用 Pico CSS 保持风格一致
- **后端验证**:
  - Step 2: 调用 `/api/qbit/test` 测试连接（已有 API）
  - Step 3: 调用 `/api/feed/add` 测试 RSS URL 有效性（已有 API）
- **状态判断**: 如果已有完整配置，提供「重新引导」入口（侧边栏底部小链接）
- **零新依赖**

### 页面结构

```
+-------------------------------------------+
|  MikanDown 设置向导                        |
|                                            |
|  [Step 1/3] [Step 2/3] [Step 3/3]         |
|                                            |
|  内容区: 当前步骤的表单                    |
|  底部: [上一步] [下一步/完成]              |
+-------------------------------------------+
```

## 二、订阅导入/导出（OPML）

### 目标
支持订阅列表的标准格式导入/导出，方便分享、备份、迁移。

### 格式规范

使用标准 OPML 2.0 格式，扩展字段通过自定义属性保留：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head>
    <title>MikanDown 订阅列表</title>
    <dateCreated>2026-05-15T00:00:00Z</dateCreated>
  </head>
  <body>
    <outline text="悠木碧のそれは違うと思う" type="rss"
             xmlUrl="https://mikanani.me/RSS/MyBangumi?token=xxx"
             htmlUrl="https://mikanani.me/Home/Episode/xxx"
             mikan_include_filter="悠木碧"
             mikan_exclude_filter="生肉,720P"/>
  </body>
</opml>
```

### API 设计

```
GET  /api/feeds/export   → 下载 OPML 文件
POST /api/feeds/import   → 上传 OPML 文件，批量导入
```

### 前端变更

- 在「添加订阅」表单区域增加两个小按钮「📥 导入 OPML」「📤 导出 OPML」
- 导入时弹出文件选择器，上传后自动刷新列表
- 导出直接下载文件

### 零新依赖（使用 Python 标准库 `xml.etree.ElementTree`）

## 三、状态仪表盘

### 目标
在主页顶部展示关键统计信息，让用户一目了然系统状态。

### 数据来源

| 指标 | 来源 | 说明 |
|------|------|------|
| 订阅数 | `config['feeds']` 长度 | 直接读取 |
| 已下载集数 | `downloaded_history.json` 长度 | 直接读取 |
| 活跃种子数 | qBittorrent API `torrents_info()` | 过滤 `state: downloading` |
| 上次更新时间 | 调度器状态 | 存储在内存变量中 |
| 磁盘用量 | (`/app/data`) 所在分区 | 可选，通过 `shutil.disk_usage` |

### API 设计

```
GET /api/status → {
  "feed_count": 12,
  "downloaded_total": 348,
  "active_torrents": 5,
  "last_update": "2026-05-15T10:30:00",
  "disk_usage": {"total": 500, "used": 200, "free": 300, "unit": "GB"}
}
```

### 前端展示

在 `index.html` 顶部增加状态栏：

```
+------------------------------------------------------------------+
| 📡 12 订阅 | 📥 348 已下载 | 💾 5 活跃种子 | ⏱ 2 分钟前更新 |
+------------------------------------------------------------------+
```

- 使用 Pico CSS 的 `grid` 布局，响应式适配窄屏
- 页面加载时调用一次 `/api/status`，不自动轮询
- 点击「手动更新」后自动刷新状态栏

### 后端变更

- `main.py` 中新增全局变量 `last_update_time`，调度器运行时更新
- 新增 `/api/status` 路由
- 加载历史记录计算下载总数（已有 `load_history` 函数）

### 零新依赖

## 实现顺序建议

1. **状态仪表盘** — 改动最小，快速见效，适合第一个做
2. **配置向导** — 影响用户体验最大，适合第二个做
3. **OPML 导入/导出** — 独立功能，优先级可调

## 明确不做的事

（范围边界）

- 不做前端框架迁移（Vue/React）— 属于方案 C
- 不做数据库迁移 — 属于方案 C
- 不重建后端架构
- 不引入 TypeScript 或构建工具
