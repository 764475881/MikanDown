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
    """从二级缓存读取，过期返回 None"""
    cache = _load_cache()
    entry = cache.get(key)
    if entry is None:
        return None
    if time.time() - entry.get('fetched_at', 0) > ttl:
        return None
    return entry.get('data')


def _set_cache(key: str, data: Any) -> None:
    """写入二级缓存"""
    cache = _load_cache()
    cache[key] = {'data': data, 'fetched_at': time.time()}
    _save_cache(cache)


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
        return cached

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

    search_url = "https://mikanani.me/Home/Classic?searchstr={}"

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
    result = _search(title_cn, proxy)
    if result:
        return result
    # 中文没结果，试日文名
    if title_jp and title_jp != title_cn:
        result = _search(title_jp, proxy)
        if result:
            return result
    return None
