# 当季番页面 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法。

**目标：** 在 MikanDown 中新增「当季番」Tab，展示 Bangumi API 获取的当季放送番剧，自动匹配 Mikan RSS 链接，支持一键订阅 + 下载所有已有剧集。

**架构：** Bangumi `/v0/calendar` 获取元数据 → 搜索 Mikan 匹配 RSS 链接 → Flask API 返回合并数据 → 前端 Tab 切换展示卡片网格。订阅时复用现有 `add_feed` 逻辑 + 触发 `process_all_feeds` 下载。

**技术栈：** Python Flask, Bangumi API v0, Mikan RSS, Tabler CSS, curl_cffi, qbittorrent-api

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `bangumi_api.py` | 修改 | 新增 `get_calendar()` 和 `search_mikan_rss()` |
| `main.py` | 修改 | 新增 `GET /api/season` 和 `POST /api/season/subscribe` |
| `templates/index.html` | 修改 | 新增 Tab 导航、当季番面板 HTML |
| (CSS 内联在 index.html) | 修改 | 新增 Tab 切换、卡片网格、各个状态按钮样式 |
| (JS 内联在 index.html) | 修改 | 新增 season 数据加载、渲染、订阅交互 |

---

### 任务 1: bangumi_api.py — 新增 `get_calendar()`

**文件：** 修改 `bangumi_api.py`

- [ ] **步骤 1: 在 `bangumi_api.py` 的常量区添加 `CALENDAR_CACHE_TTL`**

在 `SEARCH_CACHE_TTL = 86400` 之后添加：

```python
CALENDAR_CACHE_TTL = 21600          # 6h — 当季放送日历
MIKAN_CACHE_TTL = 86400            # 24h — Mikan 搜索匹配结果
```

- [ ] **步骤 2: 在文件末尾添加 `get_calendar()` 函数**

在 `extract_episode_number` 函数之后添加：

```python
def get_calendar() -> dict[int, list[dict]]:
    """
    获取当季放送日历。
    从 Bangumi /v0/calendar 获取，按 weekday_id(1-7) 分组返回。
    缓存 6 小时。

    返回格式:
    {
        1: [{subject_id, name, name_cn, image, summary, rating, air_weekday}, ...],  # 周一
        2: [...],  # 周二
        ...
        7: [...],  # 周日
    }
    """
    cache_key = 'calendar'
    cached = _get_cached(cache_key, CALENDAR_CACHE_TTL)
    if cached is not None:
        logger.info("Bangumi 日历缓存命中")
        return cached

    logger.info("Bangumi 获取当季放送日历")
    try:
        resp = cffi_requests.get(
            f'{API_BASE}/v0/calendar',
            impersonate='chrome124',
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        result: dict[int, list[dict]] = {}
        for day_data in data:
            weekday = day_data['weekday']['id']  # 1=周一 ... 7=周日
            items = []
            for item in day_data.get('items', []):
                items.append({
                    'subject_id': item['id'],
                    'name': item.get('name', ''),
                    'name_cn': item.get('name_cn', ''),
                    'image': item.get('images', {}).get('common', ''),
                    'summary': item.get('summary', ''),
                    'rating': item.get('rating', {}).get('score', 0),
                    'air_weekday': weekday,
                })
            result[weekday] = items

        _set_cache(cache_key, result)
        return result
    except Exception as e:
        logger.error(f"Bangumi 获取日历失败: {e}")
        return {}


def search_mikan_rss(title_cn: str, title_jp: str) -> str | None:
    """
    搜索 Mikan 匹配番剧的 RSS 订阅链接。
    先用中文标题搜索，失败则用日文/罗马音标题。
    匹配结果缓存 24 小时（按 title_cn 或 title_jp 缓存）。

    返回 RSS URL 字符串，如 "https://mikanani.me/RSS/Bangumi?bangumiId=12345"。
    未匹配到返回 None。
    """
    from urllib.parse import quote as url_quote

    search_url = "https://mikanani.me/Home/Classic?searchstr={}"

    def _search(title: str) -> str | None:
        cache_key = f'mikan_rss:{title.lower().strip()}'
        cached = _get_cached(cache_key, MIKAN_CACHE_TTL)
        if cached is not None:
            return cached

        try:
            full_url = search_url.format(url_quote(title))
            logger.info(f"Mikan 搜索: {title} -> {full_url}")
            resp = cffi_requests.get(full_url, impersonate='chrome110', timeout=15)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.content, 'lxml')
            # Mikan Classic 搜索结果包含 /Home/Bangumi/{id} 链接
            for a_tag in soup.find_all('a', href=re.compile(r'/Home/Bangumi/\d+')):
                href = a_tag.get('href', '')
                m = re.search(r'/Home/Bangumi/(\d+)', href)
                if m:
                    bangumi_id = m.group(1)
                    rss_url = f"https://mikanani.me/RSS/Bangumi?bangumiId={bangumi_id}"
                    _set_cache(cache_key, rss_url)
                    return rss_url

            logger.info(f"Mikan 搜索未匹配: {title}")
            _set_cache(cache_key, None)  # 缓存空结果防重复请求
            return None
        except Exception as e:
            logger.error(f"Mikan 搜索失败 '{title}': {e}")
            return None

    # 先试中文名
    result = _search(title_cn)
    if result:
        return result
    # 中文没结果，试日文名
    if title_jp and title_jp != title_cn:
        result = _search(title_jp)
        if result:
            return result
    return None
```

**注意：** `search_mikan_rss` 需要用到 `BeautifulSoup`。检查 `bangumi_api.py` 顶部的导入——如果 `bs4` 未导入，添加：

```python
from bs4 import BeautifulSoup
```

在文件顶部的 `import re` 之后添加。

- [ ] **步骤 3: 验证代码语法正确**

```bash
cd /opt/git_clone/MikanDown && python3 -c "import ast; ast.parse(open('bangumi_api.py').read()); print('Syntax OK')"
```

预期输出：`Syntax OK`

- [ ] **步骤 4: Commit**

```bash
cd /opt/git_clone/MikanDown && git add bangumi_api.py && git commit -m "feat(season): add get_calendar() and search_mikan_rss() to bangumi_api"
```

---

### 任务 2: main.py — 新增 API 端点

**文件：** 修改 `main.py`

- [ ] **步骤 1: 更新导入语句**

在现有 `from bangumi_api import search_subjects, get_subject` 行中，追加 `get_calendar, search_mikan_rss`：

```python
from bangumi_api import (search_subjects, get_subject, get_calendar, search_mikan_rss)
```

并确认 `curllib` 相关导入已存在（已有 `from curl_cffi import requests as cffi_requests`）。

- [ ] **步骤 2: 在 `check_for_setup` 的豁免列表中新增 season 端点**

找到 `wizard_complete` 判断下的 `request.endpoint not in [...]` 行（约第 54 行），在列表末尾添加 `'api_season', 'season_subscribe'`：

```python
if not wizard_complete and request.endpoint not in ['wizard', 'setup', 'static', 'login', 'api_test_qbit', 'preview_feed', 'api_status', 'update_qbit_settings', 'add_feed', 'update_global_filters', 'update_proxy', 'delete_feed', 'bangumi_search', 'bangumi_set', 'feeds_missing', 'feed_missing', 'add_single_torrent', 'api_season', 'season_subscribe']:
```

- [ ] **步骤 3: 添加 `GET /api/season` 端点**

在 `add_single_torrent` 路由之后（约第 473 行）、`@app.route('/log')` 之前添加：

```python
_season_cache: dict | None = None
_season_cache_time: float = 0
_SEASON_CACHE_TTL = 600  # 10 秒 → 生产环境改为 600 (10分钟)

@app.route('/api/season')
@login_required
def api_season():
    """返回当季番剧列表，含 Bangumi 元数据 + Mikan RSS 链接 + 订阅状态"""
    global _season_cache, _season_cache_time
    now = time.time()

    # 内存缓存
    if _season_cache and (now - _season_cache_time) < _SEASON_CACHE_TTL:
        return jsonify(_season_cache)

    config = load_config()
    # feeds 的 RSS URL 集合
    subscribed_rss_urls = {feed['url'] for feed in config.get('feeds', [])}

    # 从 Bangumi 获取日历
    calendar = get_calendar()

    result = []
    weekdays = [1, 2, 3, 4, 5, 6, 7]
    for wd in weekdays:
        items = calendar.get(wd, [])
        for item in items:
            mikan_rss_url = search_mikan_rss(item['name_cn'], item['name'])
            entry = {
                'subject_id': item['subject_id'],
                'name': item['name'],
                'name_cn': item['name_cn'],
                'image': item['image'],
                'summary': item['summary'],
                'rating': item['rating'],
                'air_weekday': item['air_weekday'],
                'mikan_rss_url': mikan_rss_url,
                'is_subscribed': mikan_rss_url in subscribed_rss_urls if mikan_rss_url else False,
            }
            result.append(entry)

        # 在星期分组间添加分隔标记
        result.append({'__separator__': True, 'weekday': wd})

    _season_cache = result
    _season_cache_time = now
    return jsonify(result)
```

- [ ] **步骤 4: 添加 `POST /api/season/subscribe` 端点**

在 `api_season` 函数之后添加：

```python
@app.route('/api/season/subscribe', methods=['POST'])
@login_required
def season_subscribe():
    """订阅一部当季番剧并立即下载所有已有剧集"""
    data = request.json
    rss_url = data.get('rss_url')
    title = data.get('title', '未知番剧')

    if not rss_url:
        return jsonify({"success": False, "message": "缺少 RSS URL"}), 400

    config = load_config()

    # 检查是否已订阅
    if any(feed['url'] == rss_url for feed in config['feeds']):
        return jsonify({"success": False, "message": "已订阅该番剧"}), 409

    # 添加 feed（复用 add_feed 的简化逻辑）
    try:
        proxies_to_use = config.get('proxy') if config.get('proxy', {}).get('http') else None
        new_feed = {
            "url": rss_url,
            "title": title,
            "cover_url": "",
            "filters": {},
            "subgroup": ""
        }

        # 获取 subgroup 和封面
        response_rss = cffi_requests.get(rss_url, impersonate="chrome110", proxies=proxies_to_use, timeout=30)
        response_rss.raise_for_status()
        feed = feedparser.parse(response_rss.content)
        if feed.entries:
            match = re.search(r"^[\[【]([^\]】]+)[\]】]", feed.entries[0].title)
            if match:
                new_feed['subgroup'] = match.group(1).strip()

        # 从 URL 获取 bangumiId 获取封面
        parsed_url = urlparse(rss_url)
        query_params = parse_qs(parsed_url.query)
        bangumi_id = query_params.get('bangumiId', [None])[0]
        if bangumi_id:
            bangumi_page_url = f"https://mikanani.me/Home/Bangumi/{bangumi_id}"
            response_html = cffi_requests.get(bangumi_page_url, impersonate="chrome110", proxies=proxies_to_use, timeout=30)
            soup = BeautifulSoup(response_html.content, 'lxml')
            poster_div = soup.find('div', class_='bangumi-poster')
            if poster_div and 'style' in poster_div.attrs:
                style_match = re.search(r"url\('(.+?)'\)", poster_div['style'])
                if style_match:
                    new_feed['cover_url'] = style_match.group(1)

        config['feeds'].append(new_feed)
        save_config(config)

        # 立即触发下载：在新线程中运行 process_all_feeds（只处理这个 feed）
        def _download_new_feed():
            try:
                qbit = config.get('qbit', {})
                proxy = config.get('proxy', {})
                from backend_script import process_all_feeds
                process_all_feeds([new_feed], proxy, qbit, logger, notify_config=config.get('notify', {}))
            except Exception as e:
                logger.error(f"订阅后自动下载失败: {e}")

        thread = threading.Thread(target=_download_new_feed, daemon=True)
        thread.start()

        # 清除 season 缓存
        global _season_cache, _season_cache_time
        _season_cache = None
        _season_cache_time = 0

        return jsonify({"success": True, "message": "已订阅并开始下载"})
    except Exception as e:
        logger.error(f"订阅当季番失败 '{rss_url}': {e}")
        return jsonify({"success": False, "message": f"订阅失败: {e}"}), 500
```

- [ ] **步骤 5: 验证语法**

```bash
cd /opt/git_clone/MikanDown && python3 -c "import ast; ast.parse(open('main.py').read()); print('Syntax OK')"
```

预期输出：`Syntax OK`

- [ ] **步骤 6: Commit**

```bash
cd /opt/git_clone/MikanDown && git add main.py && git commit -m "feat(season): add /api/season and /api/season/subscribe endpoints"
```

---

### 任务 3: index.html — Tab 导航 + 当季番面板 HTML

**文件：** 修改 `templates/index.html`

- [ ] **步骤 1: 添加 Tab 导航结构到 `.main-content` 顶部**

在 `index.html` 中，找到 `<main class="main-content" id="main-content">` 下面的内容。把 `topbar` 和其后的内容用 `tab-面板` 包起来。

当前结构：
```html
<main class="main-content">
    <div class="topbar">...</div>
    <div class="status-cards">...</div>
    <div id="feeds-container">...</div>
    ...
</main>
```

修改为：

找到 `<div class="topbar">` 这一行，在其之前添加 Tab 导航：

```html
<main class="main-content" id="main-content">
    <!-- Tab 导航 -->
    <div class="season-tabs">
        <a class="season-tab active" data-tab="subscriptions">📋 当前订阅</a>
        <a class="season-tab" data-tab="season">📺 当季番</a>
    </div>
```

然后找到 `</main>` 之前，添加两个 tab 的内容容器：

把现有内容（从 `<div class="topbar">` 到 `</main>` 之前）包在 `#tab-subscriptions` 中。

当前在 topbar 之前改为：

```html
</div>  <!-- 关闭 season-tabs -->

<!-- Tab 内容: 当前订阅 -->
<div id="tab-subscriptions" class="tab-content active">
    <div class="topbar">
        <h2>当前订阅列表</h2>
        ...
    </div>
    ...原有内容...
</div>

<!-- Tab 内容: 当季番 -->
<div id="tab-season" class="tab-content">
    <div class="topbar">
        <h2>📺 当季番</h2>
        <div class="topbar-actions">
            <button class="btn-sidebar-save" onclick="refreshSeason()" style="padding:0.35rem 0.7rem;font-size:0.8rem;cursor:pointer;">🔄 刷新</button>
        </div>
    </div>
    <div id="season-container">
        <div class="season-loading">加载中...</div>
    </div>
</div>
```

- [ ] **步骤 2: 具体的 HTML 修改操作**

执行以下精确替换。

**第 1 处：** 在 `<main class="main-content" id="main-content">` 之后，`<div class="topbar">` 之前插入 Tab 导航：

找到：
```html
<main class="main-content" id="main-content">
    <div class="topbar">
```

替换为：
```html
<main class="main-content" id="main-content">
    <!-- Tab 导航 -->
    <div class="season-tabs">
        <a class="season-tab active" data-tab="subscriptions">📋 当前订阅</a>
        <a class="season-tab" data-tab="season">📺 当季番</a>
    </div>

    <!-- 当前订阅面板 -->
    <div id="tab-subscriptions" class="tab-content active">
    <div class="topbar">
```

**第 2 处：** 在 `</main>` 之前，添加关闭 subscription tab 和新的 season tab。

找到最后一个 `</main>`（不是侧边栏的），在其之前添加：

```html
    </div>  <!-- /tab-subscriptions -->

    <!-- 当季番面板 -->
    <div id="tab-season" class="tab-content">
        <div class="topbar">
            <h2>📺 当季番</h2>
            <div class="topbar-actions">
                <button class="btn-sidebar-save" onclick="refreshSeason()" style="padding:0.35rem 0.7rem;font-size:0.8rem;cursor:pointer;">🔄 刷新</button>
            </div>
        </div>
        <div id="season-container">
            <div class="season-loading">加载中...</div>
        </div>
    </div>
```

- [ ] **步骤 3: Commit**

```bash
cd /opt/git_clone/MikanDown && git add templates/index.html && git commit -m "feat(season): add tab navigation and season panel HTML"
```

---

### 任务 4: 添加 CSS 样式

**文件：** 修改 `templates/index.html` 中的 `<style>` 块

- [ ] **步骤 1: 在 `</style>` 之前添加新样式**

在 `</style>` 标记之前插入：

```css
        /* ===== Tab 切换 ===== */
        .season-tabs { display: flex; gap: 0; margin-bottom: 1.5rem; border-bottom: 1px solid rgba(255,255,255,0.08); }
        .season-tab { padding: 0.6rem 1.2rem; font-size: 0.9rem; color: #868e96; cursor: pointer; border-bottom: 2px solid transparent; transition: all 0.2s; text-decoration: none; }
        .season-tab:hover { color: #c1c2c5; }
        .season-tab.active { color: #e64980; border-bottom-color: #e64980; font-weight: 600; }
        .tab-content { display: none; }
        .tab-content.active { display: block; }

        /* ===== 当季番卡片网格 ===== */
        .season-weekday-group { margin-bottom: 2rem; }
        .season-weekday-group h3 { color: #868e96; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 0.75rem; padding-bottom: 0.4rem; border-bottom: 1px solid rgba(255,255,255,0.06); }
        .season-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 1rem; }
        .season-card { background: #232529; border-radius: 12px; overflow: hidden; transition: transform 0.2s, box-shadow 0.2s; cursor: default; border: 1px solid rgba(255,255,255,0.06); }
        .season-card:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.3); }
        .season-card .poster { width: 100%; aspect-ratio: 3/4; object-fit: cover; display: block; background: #1a1b1e; }
        .season-card .card-body { padding: 0.6rem; }
        .season-card .card-title { color: #fff; font-size: 0.8rem; font-weight: 500; line-height: 1.3; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; margin-bottom: 0.25rem; }
        .season-card .card-title-jp { color: #6c757d; font-size: 0.65rem; display: -webkit-box; -webkit-line-clamp: 1; -webkit-box-orient: vertical; overflow: hidden; margin-bottom: 0.35rem; }
        .season-card .card-meta { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.5rem; }
        .season-card .card-rating { font-size: 0.7rem; color: #f59f00; }
        .season-card .card-rating.none { color: #6c757d; }
        .season-card .card-day { font-size: 0.65rem; background: rgba(230,73,128,0.15); color: #e64980; padding: 0.1rem 0.4rem; border-radius: 4px; }
        .season-card .card-actions { margin-top: auto; }
        .season-card .season-btn { width: 100%; padding: 0.4rem 0; border: none; border-radius: 6px; font-size: 0.75rem; cursor: pointer; transition: all 0.2s; text-align: center; }
        .season-card .season-btn.primary { background: linear-gradient(135deg, #e64980, #f06595); color: #fff; }
        .season-card .season-btn.primary:hover { opacity: 0.85; }
        .season-card .season-btn.primary:disabled { opacity: 0.5; cursor: not-allowed; }
        .season-card .season-btn.subscribed { background: rgba(52,211,153,0.15); color: #34d399; cursor: default; }
        .season-card .season-btn.missing { background: rgba(255,255,255,0.06); color: #6c757d; cursor: pointer; }
        .season-card .season-btn.missing:hover { background: rgba(255,255,255,0.1); }
        .season-loading { text-align: center; padding: 3rem; color: #6c757d; font-size: 0.9rem; }

        /* ===== 手动输入 RSS 弹窗 ===== */
        .manual-rss-input { display: flex; gap: 0.5rem; margin-top: 0.5rem; }
        .manual-rss-input input { flex: 1; background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.1); border-radius: 6px; padding: 0.35rem 0.5rem; color: #c1c2c5; font-size: 0.75rem; outline: none; }
        .manual-rss-input input:focus { border-color: #e64980; }
        .manual-rss-input button { background: linear-gradient(135deg, #e64980, #f06595); color: #fff; border: none; border-radius: 6px; padding: 0.35rem 0.6rem; font-size: 0.7rem; cursor: pointer; white-space: nowrap; }
```

- [ ] **步骤 2: Commit**

```bash
cd /opt/git_clone/MikanDown && git add templates/index.html && git commit -m "feat(season): add CSS styles for tab switcher and season card grid"
```

---

### 任务 5: 添加 JS 交互逻辑

**文件：** 修改 `templates/index.html`（内联 `<script>` 块）

- [ ] **步骤 1: 找到主 `<script>` 块末尾（`</script>` 之前），添加 Tab 切换逻辑**

在现有 `</script>` 之前添加：

```javascript
    // ===== 当季番 Tab 切换 =====
    document.querySelectorAll('.season-tab').forEach(tab => {
        tab.addEventListener('click', (e) => {
            e.preventDefault();
            // 切换 tab 样式
            document.querySelectorAll('.season-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            // 切换内容面板
            const target = tab.dataset.tab;
            document.querySelectorAll('.tab-content').forEach(tc => tc.classList.remove('active'));
            const targetPanel = document.getElementById('tab-' + target);
            if (targetPanel) {
                targetPanel.classList.add('active');
            }
            // 切到当季番时加载数据
            if (target === 'season') {
                loadSeasonData();
            }
        });
    });

    // ===== 当季番数据加载与渲染 =====
    let seasonData = null;

    async function loadSeasonData(forceRefresh = false) {
        const container = document.getElementById('season-container');
        if (!container) return;
        container.innerHTML = '<div class="season-loading">加载中...</div>';

        try {
            const resp = await fetch('/api/season' + (forceRefresh ? '?_=' + Date.now() : ''));
            if (!resp.ok) {
                container.innerHTML = '<div class="season-loading" style="color:#ef4444;">加载失败，请稍后重试</div>';
                return;
            }
            seasonData = await resp.json();
            renderSeason(seasonData);
        } catch (e) {
            container.innerHTML = '<div class="season-loading" style="color:#ef4444;">网络错误: ' + e.message + '</div>';
        }
    }

    function renderSeason(data) {
        const container = document.getElementById('season-container');
        if (!data || data.length === 0) {
            container.innerHTML = '<div class="season-loading">暂无当季番数据</div>';
            return;
        }

        const weekdayNames = { 1: '周一', 2: '周二', 3: '周三', 4: '周四', 5: '周五', 6: '周六', 7: '周日' };
        let html = '';

        // 按星期分组（数据中已按星期排序，__separator__ 标记分组边界）
        let currentWeekday = null;
        let groupItems = [];

        for (const item of data) {
            if (item.__separator__) {
                // 渲染前一组的卡片
                if (currentWeekday !== null && groupItems.length > 0) {
                    html += renderWeekdayGroup(currentWeekday, weekdayNames[currentWeekday] || '未知', groupItems);
                }
                currentWeekday = item.weekday;
                groupItems = [];
            } else {
                groupItems.push(item);
            }
        }
        // 渲染最后一组
        if (currentWeekday !== null && groupItems.length > 0) {
            html += renderWeekdayGroup(currentWeekday, weekdayNames[currentWeekday] || '未知', groupItems);
        }

        container.innerHTML = html || '<div class="season-loading">暂无当季番数据</div>';
    }

    function renderWeekdayGroup(weekday, label, items) {
        let cardsHtml = '';
        for (const item of items) {
            const rating = item.rating || 0;
            const ratingHtml = rating > 0
                ? `<span class="card-rating">⭐ ${rating.toFixed(1)}</span>`
                : `<span class="card-rating none">暂无评分</span>`;
            const posterUrl = item.image || 'https://placehold.co/240x320/1a1b1e/6c757d?text=No+Poster';

            let btnHtml = '';
            if (item.mikan_rss_url) {
                if (item.is_subscribed) {
                    btnHtml = `<button class="season-btn subscribed" disabled>✓ 已追番</button>`;
                } else {
                    btnHtml = `<button class="season-btn primary" onclick="subscribeSeason('${item.subject_id}', '${escapeHtml(item.mikan_rss_url)}', '${escapeHtml(item.name_cn || item.name)}')">🔽 追番并下载</button>`;
                }
            } else {
                btnHtml = `<button class="season-btn missing" onclick="showManualRssInput('${item.subject_id}', '${escapeHtml(item.name_cn || item.name)}')">⚠ 未匹配</button>`;
            }

            cardsHtml += `
                <div class="season-card" data-subject-id="${item.subject_id}">
                    <img class="poster" src="${posterUrl}" alt="${escapeHtml(item.name_cn || item.name)}" loading="lazy" onerror="this.src='https://placehold.co/240x320/1a1b1e/6c757d?text=Error'">
                    <div class="card-body">
                        <div class="card-title" title="${escapeHtml(item.name_cn || item.name)}">${escapeHtml(item.name_cn || '未知')}</div>
                        <div class="card-title-jp" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</div>
                        <div class="card-meta">
                            ${ratingHtml}
                            <span class="card-day">${label}</span>
                        </div>
                        <div class="card-actions">
                            ${btnHtml}
                            <div id="manual-input-${item.subject_id}" style="display:none;" class="manual-rss-input">
                                <input type="text" placeholder="输入 Mikan RSS 链接" id="manual-url-${item.subject_id}">
                                <button onclick="subscribeManual('${item.subject_id}', '${escapeHtml(item.name_cn || item.name)}')">确认</button>
                            </div>
                        </div>
                    </div>
                </div>`;
        }

        return `
            <div class="season-weekday-group">
                <h3>${label}</h3>
                <div class="season-grid">
                    ${cardsHtml}
                </div>
            </div>`;
    }

    function escapeHtml(str) {
        if (!str) return '';
        return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
    }

    let subscribing = {};

    async function subscribeSeason(subjectId, rssUrl, title) {
        if (subscribing[subjectId]) return;
        subscribing[subjectId] = true;

        const card = document.querySelector(`.season-card[data-subject-id="${subjectId}"]`);
        const btn = card ? card.querySelector('.season-btn') : null;
        if (btn) {
            btn.textContent = '处理中...';
            btn.disabled = true;
        }

        try {
            const resp = await fetch('/api/season/subscribe', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ rss_url: rssUrl, title: title })
            });
            const data = await resp.json();
            if (data.success) {
                if (btn) {
                    btn.className = 'season-btn subscribed';
                    btn.textContent = '✓ 已追番';
                    btn.disabled = true;
                }
                // 刷新侧边栏统计
                if (typeof renderFeeds === 'function') {
                    // 如果初始配置已加载，刷新 feeds
                }
            } else {
                if (btn) {
                    btn.textContent = data.message === '已订阅该番剧' ? '✓ 已追番' : '订阅失败';
                    btn.className = 'season-btn subscribed';
                    btn.disabled = true;
                }
            }
        } catch (e) {
            if (btn) {
                btn.textContent = '网络错误';
                btn.disabled = false;
            }
        } finally {
            delete subscribing[subjectId];
        }
    }

    function showManualRssInput(subjectId, title) {
        const container = document.getElementById('manual-input-' + subjectId);
        if (container) {
            container.style.display = container.style.display === 'none' ? 'flex' : 'none';
        }
    }

    async function subscribeManual(subjectId, title) {
        const input = document.getElementById('manual-url-' + subjectId);
        const url = input ? input.value.trim() : '';
        if (!url) {
            alert('请输入 RSS 链接');
            return;
        }
        await subscribeSeason(subjectId, url, title);
    }

    function refreshSeason() {
        loadSeasonData(true);
    }
```

- [ ] **步骤 2: 在页面加载时自动加载当季番数据**

在现有 `</script>` 之前的初始化代码中（`initialConfig = ...; renderFeeds(...)` 那块），添加一个当季番数据预加载（可选——用户可以点击 tab 才触发，所以不需要自动加载。但可以在 DOMContentLoaded 里做一次静默加载）。

实际上保持现状即可：Tab 切换时调用 `loadSeasonData()`。

- [ ] **步骤 3: Commit**

```bash
cd /opt/git_clone/MikanDown && git add templates/index.html && git commit -m "feat(season): add JS for tab switching and season card interactions"
```

---

### 任务 6: 端到端验证

- [ ] **步骤 1: 启动 Flask 开发服务器并测试**

```bash
cd /opt/git_clone/MikanDown && python3 main.py
```

访问 `http://localhost:5000`，登录后：
1. 确认顶部出现「📋 当前订阅」和「📺 当季番」两个 Tab
2. 点击「📺 当季番」，确认加载动画出现，等待数据加载
3. 确认卡片网格按星期分组渲染
4. 确认各卡片显示海报、标题、评分、放送日标签
5. 点击「🔽 追番并下载」，确认按钮变为「处理中...」→「✓ 已追番」
6. 切换到「当前订阅」Tab，确认原有内容不受影响
7. 切回「当季番」Tab，确认已订阅的卡片显示「✓ 已追番」

- [ ] **步骤 2: 检查日志确认 API 正常工作**

查看 `/log` 页面或控制台输出，确认：
- `Bangumi 获取当季放送日历` 日志出现
- `Mikan 搜索` 日志出现
- 订阅成功后 `订阅后自动下载` 相关日志出现

- [ ] **步骤 3: 测试缓存机制**

刷新「当季番」页面，确认控制台显示 `Bangumi 日历缓存命中` 和 `Mikan 搜索` 不再重复出现（缓存命中）。

- [ ] **步骤 4: 最终 Commit**

```bash
cd /opt/git_clone/MikanDown && git add -A && git commit -m "feat: complete season page with Bangumi calendar, Mikan matching, and one-click subscribe/download"
```
