# 当季番功能设计文档

## 概述

在 MikanDown 中新增「当季番」页面，展示 Bangumi API 获取的当季放送番剧，
自动匹配 Mikan RSS 链接，支持一键订阅 + 下载所有已有剧集。

## 数据流

```
用户点击「当季番」Tab → GET /api/season
  ├─ Bangumi /v0/calendar ── 当季放送列表（海报/评分/简介/放送日）[缓存6h]
  │     └─ 每部番 → 搜索 Mikan 匹配 RSS 链接 [缓存24h]
  └─ 返回: [{subject_id, name, name_cn, image, summary, rating, air_weekday, mikan_rss_url, is_subscribed}]

用户点击卡片 → POST /api/season/subscribe
  └─ 添加 RSS 到订阅 → 立即下载所有已有剧集
```

## 后端修改

### bangumi_api.py
- `get_calendar()` — 调用 `/v0/calendar`，返回按星期分组的番剧列表，6h 缓存
- `search_mikan_rss(title_cn, title_jp)` — 搜索 Mikan 获取 RSS 链接，24h 缓存

### main.py 新增端点
- `GET /api/season` — 返回合并后的当季番数据
- `POST /api/season/subscribe` — 订阅 + 全量下载

## 前端修改

### index.html
- 顶部导航栏新增 Tab：「📋 当前订阅」|「📺 当季番」
- Tab 切换通过 JS 控制，不刷新页面
- 当季番页面：卡片网格 + 星期分组 + 订阅按钮

### 卡片设计
- 海报（Bangumi）、中文名、日文名（hover）、评分（⭐）、放送日标签
- 状态按钮：未订阅→「🔽 追番并下载」/ 已订阅→「✓ 已追番」/ 无 RSS→「⚠ 未匹配」

## 缓存策略
| 数据 | 缓存 | TTL |
|------|------|-----|
| Bangumi 日历 | bangumi_cache.json | 6h |
| Mikan 匹配 | bangumi_cache.json | 24h |
| Season API 响应 | 内存 | 10min |

## 交互细节
- 点击「追番并下载」：按钮→"处理中..." → 完成后→"✓ 已追番"
- 匹配失败的展示「未匹配」+ 手动输入 RSS 链接入口
- 按星期分组展示（周一~周日），全部展示
