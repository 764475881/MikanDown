"""Bangumi (bgm.tv) API 集成模块 — 搜索、获取番剧信息、剧集列表，带内存 + 文件二级缓存"""

import json
import logging
import os
import re
import time
from typing import Any

from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
CACHE_FILE = os.path.join(DATA_DIR, 'bangumi_cache.json')
SEASON_RATINGS_FILE = os.path.join(DATA_DIR, 'season_ratings.json')
API_BASE = 'https://api.bgm.tv'

# 缓存有效期（秒）
SUBJECT_CACHE_TTL = 86400          # 24h — 番剧基本信息
EPISODES_CACHE_TTL = 43200        # 12h — 剧集列表
SEARCH_CACHE_TTL = 86400           # 24h — 搜索结果
CALENDAR_CACHE_TTL = 21600          # 6h — 当季放送日历
MIKAN_CACHE_TTL = 86400            # 24h — Mikan 搜索匹配结果

# ── 缓存结构 ──────────────────────────────────────────
# bangumi_cache.json:
# {
#   "search:<keyword>": {
#     "data": [...],
#     "fetched_at": 1234567890.0
#   },
#   "subject:<subject_id>": {
#     "data": {...},
#     "fetched_at": 1234567890.0
#   },
#   "episodes:<subject_id>": {
#     "data": [...],
#     "fetched_at": 1234567890.0
#   }
# }


# 内存缓存层：避免缺集检测等高频场景反复读文件
_mem_cache: dict[str, dict] = {}


def _load_cache() -> dict:
    """从文件加载 Bangumi 缓存"""
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    """持久化 Bangumi 缓存到文件"""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"保存 Bangumi 缓存失败: {e}")


def _get_cached(key: str, ttl: float) -> Any | None:
    """读取缓存：先查内存，未命中/过期再读文件。过期返回 None"""
    # 1) 内存层
    mem_entry = _mem_cache.get(key)
    if mem_entry is not None:
        if time.time() - mem_entry.get('fetched_at', 0) <= ttl:
            return mem_entry.get('data')
        # 内存条目过期，继续读文件（文件可能更新过）
    # 2) 文件层
    cache = _load_cache()
    entry = cache.get(key)
    if entry is None:
        return None
    if time.time() - entry.get('fetched_at', 0) > ttl:
        return None
    data = entry.get('data')
    _mem_cache[key] = {'data': data, 'fetched_at': entry.get('fetched_at', time.time())}
    return data


def _set_cache(key: str, data: Any) -> None:
    """写入缓存（内存 + 文件）"""
    now = time.time()
    _mem_cache[key] = {'data': data, 'fetched_at': now}
    cache = _load_cache()
    cache[key] = {'data': data, 'fetched_at': now}
    _save_cache(cache)


def invalidate_cache(prefix: str | None = None) -> None:
    """
    清除缓存。
    - prefix=None: 清空全部缓存
    - prefix='mikan_rss:': 清除所有 Mikan 搜索缓存
    """
    global _mem_cache
    cache = _load_cache()
    if prefix is None:
        _save_cache({})
        _mem_cache = {}
        logger.info("Bangumi 缓存已全部清空")
        return
    keys_to_delete = [k for k in cache if k.startswith(prefix)]
    if not keys_to_delete:
        return
    for k in keys_to_delete:
        del cache[k]
    _mem_cache = {k: v for k, v in _mem_cache.items() if not k.startswith(prefix)}
    _save_cache(cache)
    logger.info(f"Bangumi 缓存已清除 {len(keys_to_delete)} 条 (前缀: {prefix})")


def search_subjects(keyword: str, limit: int = 8) -> list[dict]:
    """
    按关键字搜索番剧。
    返回 [{subject_id, name, name_cn, images, date, eps, total_episodes}, ...]
    """
    cache_key = f'search:{keyword.lower().strip()}'
    cached = _get_cached(cache_key, SEARCH_CACHE_TTL)
    if cached is not None:
        logger.info(f"Bangumi 搜索缓存命中: {keyword}")
        return cached

    logger.info(f"Bangumi 搜索: {keyword}")
    try:
        resp = cffi_requests.post(
            f'{API_BASE}/v0/search/subjects',
            json={'keyword': keyword, 'filter': {'type': [2]}},   # type=2 = 动画
            impersonate='chrome124',
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for item in data.get('data', [])[:limit]:
            results.append({
                'subject_id': item['id'],
                'name': item.get('name', ''),
                'name_cn': item.get('name_cn', ''),
                'image': item.get('images', {}).get('common', ''),
                'date': item.get('date', ''),
                'eps': item.get('eps', 0),
                'total_episodes': item.get('total_episodes', 0),
            })
        _set_cache(cache_key, results)
        return results
    except Exception as e:
        logger.error(f"Bangumi 搜索失败 '{keyword}': {e}")
        return []


def get_subject(subject_id: int) -> dict | None:
    """
    获取单个番剧详情。
    返回 {subject_id, name, name_cn, images, date, total_episodes, eps, summary, rating, air_date}
    """
    cache_key = f'subject:{subject_id}'
    cached = _get_cached(cache_key, SUBJECT_CACHE_TTL)
    if cached is not None:
        return cached

    logger.info(f"Bangumi 获取番剧: {subject_id}")
    try:
        resp = cffi_requests.get(
            f'{API_BASE}/v0/subjects/{subject_id}',
            impersonate='chrome124',
            timeout=15,
        )
        resp.raise_for_status()
        raw = resp.json()
        result = {
            'subject_id': raw['id'],
            'name': raw.get('name', ''),
            'name_cn': raw.get('name_cn', ''),
            'image': raw.get('images', {}).get('common', ''),
            'date': raw.get('date', ''),
            'total_episodes': raw.get('total_episodes', 0),
            'eps': raw.get('eps', 0),
            'summary': raw.get('summary', ''),
            'rating': raw.get('rating', {}).get('score', 0),
        }
        _set_cache(cache_key, result)
        return result
    except Exception as e:
        logger.error(f"Bangumi 获取番剧失败 {subject_id}: {e}")
        return None


def get_episodes(subject_id: int) -> list[dict]:
    """
    获取番剧的所有剧集列表（type=0 本篇）。
    返回 [{ep, name, name_cn, air_date, id}, ...]
    注意: ep 字段对应 sort/air_episode（实际集号）
    """
    cache_key = f'episodes:{subject_id}'
    cached = _get_cached(cache_key, EPISODES_CACHE_TTL)
    if cached is not None:
        return cached

    logger.info(f"Bangumi 获取剧集列表: {subject_id}")
    episodes: list[dict] = []
    try:
        offset = 0
        limit = 100
        while True:
            resp = cffi_requests.get(
                f'{API_BASE}/v0/episodes',
                params={'subject_id': subject_id, 'type': 0, 'offset': offset, 'limit': limit},
                impersonate='chrome124',
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            for item in data.get('data', []):
                episodes.append({
                    'id': item['id'],
                    'ep': item.get('sort', 0),           # 实际集号
                    'name': item.get('name', ''),
                    'name_cn': item.get('name_cn', ''),
                    'air_date': item.get('airdate', ''),
                })
            total = data.get('total', 0)
            offset += len(data.get('data', []))
            if offset >= total:
                break
        _set_cache(cache_key, episodes)
        return episodes
    except Exception as e:
        logger.error(f"Bangumi 获取剧集列表失败 {subject_id}: {e}")
        return []


# ── 辅助：从 RSS 标题解析集号 ──────────────────────────

def extract_episode_number(title: str) -> int | None:
    """
    从 Mikan Project RSS 标题中提取集号。
    支持的格式:
      [SubGroup] Anime Name - 01 [1080p][...]
      [SubGroup] Anime Name S2 - 01 [1080p][...]
      [SubGroup] Anime Name 第01话 [1080p][...]
      [SubGroup] Anime Name 01 [1080p][...]
    返回集号（int），未找到返回 None。
    """
    # 模式1: - 01 (数字前有连字符/空格)
    m = re.search(r'[-\s](\d{2,3})(?=\s*[\[\(])', title)
    if m:
        return int(m.group(1))

    # 模式2: 第01话 / 第01集
    m = re.search(r'第\s*(\d+)\s*[话話集]', title)
    if m:
        return int(m.group(1))

    # 模式3: 纯数字在末尾（带空格）
    m = re.search(r'\s(\d{2,3})\s*$', title)
    if m:
        return int(m.group(1))

    # 模式4: 独立方括号集号 [01]（如喵萌奶茶屋: ...Shinai][01][1080p][繁日双语]）
    # 排除分辨率/年份等大数字
    m = re.search(r'\[(\d{1,3})\](?=\s*\[)', title)
    if m and int(m.group(1)) <= 500:
        return int(m.group(1))

    return None


def get_calendar(proxy: dict | None = None) -> dict[int, list[dict]]:
    """
    获取当季放送日历。
    从 Bangumi /calendar 获取，按 weekday_id(1-7) 分组返回。
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
        # JSON 缓存会丢失 int key 类型，转回来
        return {int(k): v for k, v in cached.items()}

    logger.info("Bangumi 获取当季放送日历")
    try:
        kwargs = {
            'impersonate': 'chrome124',
            'timeout': 15,
        }
        if proxy:
            kwargs['proxies'] = proxy
        resp = cffi_requests.get(f'{API_BASE}/calendar', **kwargs)
        if resp is None:
            raise RuntimeError(f"curl_cffi.get() returned None (kwargs={kwargs})")
        resp.raise_for_status()
        data = resp.json()

        result: dict[int, list[dict]] = {}
        for day_data in data:
            weekday = day_data['weekday']['id']  # 1=周一 ... 7=周日
            items = []
            for item in day_data.get('items', []):
                if item is None:
                    continue
                items.append({
                    'subject_id': item['id'],
                    'name': item.get('name', ''),
                    'name_cn': item.get('name_cn', ''),
                    'image': (item.get('images') or {}).get('common', ''),
                    'summary': item.get('summary', ''),
                    'rating': (item.get('rating') or {}).get('score', 0),
                    'air_weekday': weekday,
                })
            result[weekday] = items

        _set_cache(cache_key, result)
        return result
    except Exception as e:
        logger.error(f"Bangumi 获取日历失败: {e}")
        return {}


def search_mikan_rss(title_cn: str, title_jp: str, proxy: dict | None = None) -> str | None:
    """
    搜索 Mikan 匹配番剧的 RSS 订阅链接。
    先用中文标题搜索，失败则用日文/罗马音标题。
    匹配结果缓存 24 小时（按 title_cn 或 title_jp 缓存）。

    返回 RSS URL 字符串，如 "https://mikanani.me/RSS/Bangumi?bangumiId=12345"。
    未匹配到返回 None。
    """
    from urllib.parse import quote as url_quote

    search_url = "https://mikanani.me/Home/Search?searchstr={}"

    def _search(title: str, proxy: dict | None = None) -> str | None:
        cache_key = f'mikan_rss:{title.lower().strip()}'
        cached = _get_cached(cache_key, MIKAN_CACHE_TTL)
        if cached is not None:
            return cached

        try:
            full_url = search_url.format(url_quote(title))
            logger.info(f"Mikan 搜索: {title} -> {full_url}")
            kwargs = {'impersonate': 'chrome110', 'timeout': 15}
            if proxy:
                kwargs['proxies'] = proxy
            resp = cffi_requests.get(full_url, **kwargs)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.content, 'lxml')
            # Mikan Search 搜索结果包含 /Home/Bangumi/{id} 链接
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
    result = _search(title_cn, proxy)
    if result:
        return result

    # 中文名太长搜不到？Mikan 搜索有关键字数限制，尝试截断末尾
    if title_cn and len(title_cn) >= 8:
        for truncate_len in range(len(title_cn) - 2, 5, -2):  # 逐次去掉末尾2个字
            short_title = title_cn[:truncate_len]
            logger.info(f"Mikan 截断搜索: {short_title}")
            result = _search(short_title, proxy)
            if result:
                return result

    # 中文没结果，试日文名
    if title_jp and title_jp != title_cn:
        result = _search(title_jp, proxy)
        if result:
            return result
    return None


def get_mikan_season_list(proxy: dict | None = None) -> list[dict]:
    """
    爬取 Mikan 首页，获取当季番列表（按星期几/剧场版分组，与 Mikan 首页一致）。
    只需一次请求，避免逐个搜索。

    返回: [{title, bangumi_id, rss_url, mikan_poster_url, weekday, last_update}, ...]
    weekday: 1=周一 ... 7=周日, 0=剧场版/OVA
    """
    cache_key = 'mikan_homepage'
    cached = _get_cached(cache_key, MIKAN_CACHE_TTL)
    if cached is not None:
        # 旧版本缓存缺少 weekday/has_resource 字段，视为过期重新爬取
        if cached and isinstance(cached[0], dict) and ('weekday' not in cached[0] or 'has_resource' not in cached[0]):
            cached = None
        else:
            return cached

    try:
        kwargs = {'impersonate': 'chrome110', 'timeout': 15}
        if proxy:
            kwargs['proxies'] = proxy
        resp = cffi_requests.get('https://mikanani.me/', **kwargs)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.content, 'lxml')

        # 1) 星期分组：m-home-week-item（星期一~星期日 + 剧场版）
        weekday_map = {'星期一': 1, '星期二': 2, '星期三': 3, '星期四': 4,
                       '星期五': 5, '星期六': 6, '星期日': 7,
                       '剧场版': 0, 'OVA': 0, 'OVA/剧场版 (beta)': 0}
        items: list[dict] = []
        seen_ids: set[int] = set()
        seen_titles: set[str] = set()  # 无资源番剧无 bangumi_id，用标题去重

        for item in soup.select('div.m-home-week-item'):
            title_el = item.select_one('.title')
            if not title_el:
                continue
            label = title_el.get_text(strip=True)
            weekday = weekday_map.get(label)
            if weekday is None:
                continue
            for sq in item.select('.m-week-square'):
                a = sq.select_one('a[href*="/Home/Bangumi/"]')
                greyout = sq.select_one('.greyout') is not None
                if a:
                    m = re.search(r'/Home/Bangumi/(\d+)', a['href'])
                    if not m:
                        continue
                    bangumi_id = int(m.group(1))
                    if bangumi_id in seen_ids:
                        continue
                    seen_ids.add(bangumi_id)
                    title = (a.get('title') or '').strip() or a.get_text(strip=True)
                    rss_url = f"https://mikanani.me/RSS/Bangumi?bangumiId={bangumi_id}"
                elif greyout:
                    # 无字幕组资源的番剧：链接是 javascript:void(0)，只有标题和海报
                    title = (a.get('title') or '').strip() if a else ''
                    if not title:
                        st = sq.select_one('.small-title')
                        title = st.get_text(strip=True) if st else ''
                    if not title or title in seen_titles:
                        continue
                    seen_titles.add(title)
                    bangumi_id = None
                    rss_url = ''
                else:
                    continue
                # 海报 URL：m-week-square 内懒加载 data-src（去掉尺寸参数）
                poster_rel = ''
                img = sq.select_one('img[data-src]')
                if img:
                    poster_rel = (img['data-src'] or '').split('?')[0].strip()
                poster_url = f"https://mikanani.me{poster_rel}" if poster_rel and poster_rel.startswith('/') else ''
                items.append({
                    'title': title,
                    'bangumi_id': bangumi_id,
                    'rss_url': rss_url,
                    'mikan_poster_url': poster_url,
                    'weekday': weekday,
                    'last_update': '',
                    'has_resource': bangumi_id is not None,
                })

        # 2) 从"最近更新"列表（ul.an-ul）合并 last_update，按 bangumi_id 关联
        update_map: dict[int, str] = {}
        for li in soup.select('ul.an-ul li'):
            span = li.find('span', attrs={'data-bangumiid': True})
            if not span:
                continue
            try:
                bid = int(span['data-bangumiid'].strip())
            except (ValueError, KeyError):
                continue
            date_el = li.select_one('.date-text')
            if date_el:
                update_map[bid] = date_el.get_text(strip=True).replace('更新', '').strip()
        for it in items:
            if it['bangumi_id'] in update_map:
                it['last_update'] = update_map[it['bangumi_id']]

        # 同时预热单个搜索缓存（仅限有资源的番剧）
        for item in items:
            if not item.get('has_resource'):
                continue
            lower = item['title'].lower().strip()
            _set_cache(f'mikan_rss:{lower}', item['rss_url'])
            _set_cache(f'mikan_rss:{lower.replace(" ", "").replace("　", "")}', item['rss_url'])

        logger.info(f"Mikan 首页: 爬取到 {len(items)} 个番剧")
        _set_cache(cache_key, items)
        return items
    except Exception as e:
        logger.error(f"Mikan 首页爬取失败: {e}")
        return []


# ── 当季番评分缓存 ────────────────────────────────────
# season_ratings.json:
# {
#   "12345": {"score": 7.8, "name": "标题", "fetched_at": 1234567890.0, "attempts": 1}
# }
# score=0 表示抓取过但 Bangumi 无评分（或抓取失败）；attempts 记录累计尝试次数
SEASON_RATING_TTL = 24 * 3600      # 有分缓存 24h → 每天凌晨刷新一次
SEASON_RATING_RETRY_TTL = 600      # 无分重试冷却 10 分钟（"没有分时立马更新一次"）
SEASON_RATING_FAIL_TTL = 24 * 3600 # 多次失败后降级为每天重试一次


def load_season_ratings() -> dict:
    """读取评分缓存文件。返回 {str(bangumi_id): {score, name, fetched_at, attempts}}"""
    try:
        with open(SEASON_RATINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_season_ratings(ratings: dict) -> None:
    """持久化评分缓存"""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(SEASON_RATINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(ratings, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"保存评分缓存失败: {e}")


def rating_needs_fetch(ratings: dict, bangumi_id: int) -> bool:
    """判断该番剧评分是否值得重新抓取：
    - 从未抓过 → 立即抓
    - 有分 → 超过 24h（每天凌晨刷新）
    - 无分 → 尝试 <3 次且冷却 10 分钟过 → 立即重试；否则降级为 24h 一次"""
    r = ratings.get(str(bangumi_id))
    now = time.time()
    if r is None:
        return True
    fetched_at = r.get('fetched_at', 0)
    attempts = r.get('attempts', 0)
    if r.get('score', 0) > 0:
        return (now - fetched_at) > SEASON_RATING_TTL
    if attempts < 3:
        return (now - fetched_at) > SEASON_RATING_RETRY_TTL
    return (now - fetched_at) > SEASON_RATING_FAIL_TTL


def fetch_subject_rating(bangumi_id: int, name: str = '', proxy: dict | None = None) -> float:
    """
    直接调 Bangumi API 抓取番剧评分（绕过 subject 缓存，只写评分缓存文件）。
    返回评分（0-10 浮点）；失败或无评分返回 0。
    """
    try:
        kwargs = {'impersonate': 'chrome124', 'timeout': 15}
        if proxy:
            kwargs['proxies'] = proxy
        resp = cffi_requests.get(f'{API_BASE}/v0/subjects/{bangumi_id}', **kwargs)
        resp.raise_for_status()
        raw = resp.json()
        score = float((raw.get('rating') or {}).get('score') or 0)
        ratings = load_season_ratings()
        entry = ratings.get(str(bangumi_id), {})
        entry.update({
            'score': score,
            'name': name or raw.get('name_cn') or raw.get('name', ''),
            'fetched_at': time.time(),
            'attempts': entry.get('attempts', 0) + 1,
        })
        ratings[str(bangumi_id)] = entry
        save_season_ratings(ratings)
        logger.info(f"评分抓取 {bangumi_id} ({entry['name']}): {score}")
        return score
    except Exception as e:
        logger.error(f"评分抓取失败 {bangumi_id}: {e}")
        # 失败也记录 fetched_at，避免立刻反复重试
        ratings = load_season_ratings()
        entry = ratings.get(str(bangumi_id), {})
        entry.update({
            'score': entry.get('score', 0),
            'name': name or entry.get('name', ''),
            'fetched_at': time.time(),
            'attempts': entry.get('attempts', 0) + 1,
        })
        ratings[str(bangumi_id)] = entry
        save_season_ratings(ratings)
        return 0
