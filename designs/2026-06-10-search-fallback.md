# Bangumi 搜索兜底 — 当季番剧页面元数据补全

## 背景

当季番剧页面（`api/season`）需要为每个 Mikan 番剧条目补充 Bangumi 元数据（海报、评分、中文名等）。原有逻辑只通过 `find_bangumi_meta()` 在 Bangumi 放送日历中做标题匹配，但日历收录并不完整 —— 跨季番剧、非档期番剧、OVA/剧场版等不在日历中，导致大量条目缺少海报。

## 目标

- 对未匹配到日历的番剧，通过 Bangumi 搜索 API 兜底
- 确保搜索匹配准确，避免误配
- 最终达到 95%+ 的海报覆盖率

## 方案

### 数据流

```
Mikan 首页爬取 (89 部)
    │
    ├─ Bangumi 日历匹配 → 命中 (68 部, 含 weekday 信息)
    │
    └─ 未命中 (21 部)
         │
         └─ Bangumi Search API 搜索
              │
              ├─ CJK 重叠验证 ≥55% → 采用 (≈18 部)
              │
              └─ 验证不通过 → 丢弃 (≈3 部)
```

### 搜索策略

1. 以 Mikan 标题原文调用 Bangumi `search/subjects` API
2. 对搜索结果按 **CJK 字符重叠率** 验证

### CJK 重叠验证

为防止混淆（例如"与爱丽丝梦游仙境"误配到"爱丽丝与藏六"），只比较中日韩统一表意文字（`\u4e00-\u9fff`）的字符集合：

```
query_cjk = set(CJK_chars_in(mikan_title))
result_cjk = set(CJK_chars_in(bangumi_name_cn))
overlap = |query_cjk ∩ result_cjk| / max(|query_cjk|, |result_cjk|, 1)
```

**阈值选择：55%**

| Mikan 标题 | Bangumi 结果 | 重叠率 | 结论 |
|---|---|---|---|
| 剧场版总集篇 GIRLS BAND CRY 呐、未来。 | 剧场版总集篇 少女乐队的呐喊 呐、未来。 | 64% | ✅ 正确接受 |
| 姬骑士是蛮族的新娘 | 女骑士成为蛮族新娘 | 67% | ✅ 正确接受 |
| 加油吧！中村君！！ | 加油吧！中村君！！ | 100% | ✅ 正确接受 |
| 与爱丽丝梦游仙境 | 爱丽丝与藏六 | 50% | ❌ 正确拒绝 |
| 纯洁庞克发条少女 | 纯洁的玛利亚 | 25% | ❌ 正确拒绝 |

### 匹配结果兜底字段

搜索匹配成功后，以下字段被补充到条目中（`air_weekday=0` 标记为未分类）：

```python
meta = {
    'subject_id': s['subject_id'],
    'name': s['name'],          # 日文/英文名
    'name_cn': s['name_cn'],    # 中文名
    'image': s['image'],        # 海报 URL
    'summary': '',
    'rating': 0,
    'air_weekday': 0,           # 未分类
}
```

## 效果

| 指标 | 优化前 | 优化后 |
|---|---|---|
| 日历匹配 | 68 (76.4%) | 68 (76.4%) |
| 搜索兜底 | 0 | ≈18 (21.4%) |
| 无元数据 | 21 (23.6%) | ≈3 (3.4%) |
| **总覆盖率** | **76.4%** | **≈96.6%** |

### 已知未匹配（3 个）

- **哆啦A梦** — Bangumi 搜索只返回电影版，无 TV 版匹配
- **与爱丽丝梦游仙境 -Dive in Wonderland-** — Bangumi 搜索不返回该条目
- **纯洁庞克 发条少女** — Bangumi 无对应条目

## 风险

- Bangumi 搜索 API 较慢（单次 ~1-5s），首次冷启动需 ~2-3 分钟。结果会缓存到 `bangumi_cache.json`，后续请求秒级响应。
- 当 Bangumi 网络不可用时，搜索也会失败，覆盖率降回日历匹配水平。

## 代码变更

`main.py` — 在 `api_season()` 中，`find_bangumi_meta()` 返回 `None` 时新增搜索兜底逻辑。
`data/bangumi_cache.json` — 自动缓存新的搜索结果。
