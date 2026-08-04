# --- 1. 导入必要的库 ---
import re

import feedparser  # 用于解析 RSS 和 Atom Feed
import json  # 用于处理 JSON 数据 (历史记录文件)
import time  # 用于在循环中添加延迟
from qbittorrentapi import Client  # qBittorrent 的 API 客户端库
from curl_cffi import requests as cffi_requests  # 模拟浏览器的网络请求库，用于绕过网站防火墙
import os
from notifier import send_notification
from bangumi_api import extract_episode_number
# --- 2. 全局常量 ---

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 定义历史记录文件的路径，方便统一管理
DATA_DIR = os.path.join(BASE_DIR, 'data')
HISTORY_FILE = os.path.join(DATA_DIR, 'downloaded_history.json')

# --- 3. 辅助函数 (读写历史记录) ---
def load_history():
    """从 data/downloaded_history.json 文件加载历史记录对象列表。"""
    try:
        # 以只读模式('r')和 utf-8 编码打开文件
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            # 读取文件内容并将其解析为 Python 列表
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        # 如果文件不存在或内容不是有效的 JSON，返回一个空列表，避免程序崩溃
        return []

def save_history(history_list):
    """将历史记录对象列表保存到 data/downloaded_history.json 文件。"""
    # 以写入模式('w')和 utf-8 编码打开文件
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        # 将 Python 列表转换为格式化的 JSON 字符串并写入文件
        # indent=4 使 JSON 文件格式优美，易于阅读
        # ensure_ascii=False 确保中文字符能被正确写入
        json.dump(history_list, f, indent=4, ensure_ascii=False)


def get_season_string(title: str) -> str | None:
    """
    从一个标题字符串中提取季度信息，并返回 "Season X" 格式的字符串。
    如果找不到匹配的季度，则返回 None。
    """
    # 定义中文数字到阿拉伯数字的映射
    chinese_num_map = {
        '一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
        '六': 6, '七': 7, '八': 8, '九': 9, '十': 10
    }

    # 正则表达式，用于捕获季度数字（中文或阿拉伯数字）
    regex = re.compile(r"第\s*([一二三四五六七八九十\d]+)\s*季")
    match = regex.search(title)

    if not match:
        return "Season 1"

    season_part = match.group(1) # 捕获到的季度部分，例如 "1" 或 "三"

    # 检查捕获到的部分是阿拉伯数字还是中文数字
    if season_part.isdigit():
        season_number = int(season_part)
    else:
        # 如果是中文，从映射字典中查找
        season_number = chinese_num_map.get(season_part)

    # 如果成功转换了数字，则格式化并返回字符串
    if season_number is not None:
        return f"Season {season_number}"
    else:
        # 如果中文数字不在我们的映射中（例如"十一"），则返回 Season 1
        return "Season 1"

def clean_category_name(title: str) -> tuple[str, str]:
    """从番剧标题中分离季度/部分信息，返回 (干净的分类名, 季度标识)。

    例如:
      "进击的巨人 第三季"         → ("进击的巨人", "Season 3")
      "鬼灭之刃 第二季 游郭篇"    → ("鬼灭之刃 游郭篇", "Season 2")
      "咒术回战"                   → ("咒术回战", "Season 1")
    """
    regex_season = re.compile(r"(第\s*[一二三四五六七八九十\d]+\s*季)")
    regex_part = re.compile(r"(第\s*[一二三四五六七八九十\d]+\s*部分)")

    session = get_season_string(title)
    category = title

    found_season = regex_season.search(category)
    if found_season:
        category = category.replace(found_season[0], '').strip()

    found_part = regex_part.search(category)
    if found_part:
        category = category.replace(found_part[0], '').strip()

    return category, session


def extract_save_path(download_path_base: str, category: str, session: str) -> str:
    """构造 qBittorrent 保存路径。"""
    return f"{download_path_base.rstrip('/')}/{category}/{session}/"


def get_magnet_from_torrent_url(torrent_url: str, logger) -> str | None:
    """
    从 Mikan 的 .torrent URL 提取 magnet link。
    Mikan 的 .torrent 下载需要登录 Cookie，qB 无法直接获取，
    所以我们先请求剧集页面提取 magnet link。
    """
    import re as _re
    # URL pattern: /Download/YYYYMMDD/{hash}.torrent
    m = _re.search(r'/\d{8}/([a-f0-9]{40})\.torrent', torrent_url)
    if not m:
        logger.warning(f"  -> ⚠️ 无法从 torrent URL 提取 episode hash: {torrent_url}")
        return None
    episode_hash = m.group(1)
    page_url = f"https://mikanani.me/Home/Episode/{episode_hash}"
    try:
        r = cffi_requests.get(page_url, impersonate='chrome110', timeout=15)
        if r.status_code != 200:
            logger.warning(f"  -> ⚠️ 剧集页面返回 {r.status_code}: {page_url}")
            return None
        mm = _re.search(r'href=["\'](magnet:\?[^\s"\'<>]+)["\']', r.text)
        if mm:
            magnet = mm.group(1).replace('&amp;', '&')
            logger.info(f"  -> ✅ 从剧集页面提取到 magnet link")
            return magnet
        else:
            logger.warning(f"  -> ⚠️ 剧集页面未找到 magnet link: {page_url}")
            return None
    except Exception as e:
        logger.warning(f"  -> ⚠️ 获取剧集页面失败: {e}")
        return None


def extract_info_hash_from_magnet(magnet_url: str) -> str | None:
    """
    从 magnet link 提取 btih info-hash，用于反向确认 qBittorrent 是否已接收该种子。
    返回 40 位十六进制（小写）；base32 变体返回 32 位大写。无法提取返回 None。
    """
    if not magnet_url:
        return None
    m = re.search(r'xt=urn:btih:([a-fA-F0-9]{40})', magnet_url)
    if m:
        return m.group(1).lower()
    m = re.search(r'xt=urn:btih:([a-z2-7]{32})', magnet_url)
    if m:
        return m.group(1).upper()
    return None


# --- 4. 核心处理函数 ---
def process_all_feeds(feed_objects, proxy_config, qbit_config, logger, notify_config=None):
    """
    处理所有给定的 Feed 对象列表，检查更新并添加到 qBittorrent。
    这个函数是整个后台下载逻辑的核心，被主应用 `main.py` 在手动或定时任务中调用。

    :param feed_objects: 包含每个订阅详细信息的对象列表 (url, title, filters 等)。
    :param proxy_config: 包含代理服务器设置的字典。
    :param qbit_config: 包含 qBittorrent 连接信息的字典。
    :param logger: 从主应用传入的日志记录器实例。
    :return: bool —— True 表示任务正常完成（含"无新项目"）；False 表示发生致命错误
             （qBittorrent 连接失败或未捕获异常）。调用方（如添加订阅后的后台任务）
             可据此向前端反馈真实结果，避免"误报成功"。
    """
    try:
        return _do_process_all_feeds(feed_objects, proxy_config, qbit_config, logger, notify_config)
    except Exception as e:
        import traceback
        logger.error(f"❌ process_all_feeds 异常: {e}")
        logger.error(traceback.format_exc())
        return False


def _do_process_all_feeds(feed_objects, proxy_config, qbit_config, logger, notify_config=None):
    logger.info("--- 开始新一轮的 RSS 检查任务 ---")

    # 从传入的配置字典中安全地获取 qBittorrent 连接信息
    qbit_host = qbit_config.get('host')
    qbit_port = qbit_config.get('port')
    qbit_user = qbit_config.get('username')
    qbit_pass = qbit_config.get('password')
    # 如果未指定根保存路径，则提供一个默认值
    download_path_base = qbit_config.get('save_path_base', '/downloads/')

    # 尝试连接到 qBittorrent 客户端
    try:
        qbt_client = Client(host=qbit_host, port=int(qbit_port) if qbit_port else 9888, username=qbit_user, password=qbit_pass, VERIFY_WEBUI_CERTIFICATE=False, REQUESTS_ARGS={'timeout': (10, 30)})
        qbt_client.auth_log_in()
        logger.info("✅ 成功连接到 qBittorrent。")
    except Exception as e:
        # 如果连接失败，记录错误并中止本次任务
        logger.error(f"❌ 连接 qBittorrent 失败: {e} (请检查侧边栏中的 qBittorrent 设置)")
        return False

    # 加载完整的历史记录对象列表
    downloaded_history_list = load_history()
    # 为了快速查找，我们从历史记录中提取出一个只包含 URL 的集合 (set)
    # 集合的查找速度远快于列表
    known_urls = {item['url'] for item in downloaded_history_list}
    logger.info(f"已加载 {len(known_urls)} 条历史记录。")

    # 判断是否需要使用代理
    proxies_to_use = proxy_config if proxy_config and proxy_config.get('http') else None

    # 计数器，用于判断本轮任务是否有新下载
    new_downloads_this_run = 0

    # 遍历 `main.py` 传入的每一个订阅对象
    for feed_item in feed_objects:
        # 从订阅对象中提取所需信息
        url = feed_item.get('url')
        feed_title = feed_item.get('title', '未知番剧')
        subgroup = feed_item.get('subgroup', '')
        filters = feed_item.get('filters', {})
        # 将关键词字符串按空格分割成列表，并过滤掉空字符串
        include_keywords = [k for k in filters.get('include', '').split() if k]
        exclude_keywords = [k for k in filters.get('exclude', '').split() if k]

        # 使用共享函数清洗分类名
        qbit_category, session = clean_category_name(feed_title)
        save_path = extract_save_path(download_path_base, qbit_category, session)
        logger.info(f"分类名: '{qbit_category}' | 保存路径: '{save_path}'")

        logger.info(f"--- 正在处理 Feed: {qbit_category} ---")
        if include_keywords or exclude_keywords:
            logger.info(f"  - 应用规则: 包含[{' '.join(include_keywords)}] 排除[{' '.join(exclude_keywords)}]")

        response = None
        max_retries = 2 # 设置最大重试次数为2次
        # 网络请求的重试循环，以应对临时的网络问题
        for attempt in range(max_retries):
            try:
                # 使用 cffi_requests 发起请求，impersonate 参数使其模拟浏览器，防止被屏蔽
                response = cffi_requests.get(url, impersonate="chrome110", timeout=30, proxies=proxies_to_use)
                # 检查 HTTP 状态码，如果不是 200 (成功)，则抛出异常
                if response.status_code == 200:
                    break # 成功获取，跳出重试循环
                else:
                    raise ConnectionError(f"HTTP 状态码: {response.status_code}")
            except Exception as e:
                logger.warning(f"第 {attempt + 1} 次尝试处理 Feed '{url}' 时发生错误: {e}")
                if attempt < max_retries - 1:
                    logger.info("将在5秒后重试...")
                    time.sleep(5) # 重试前等待5秒
                else:
                    logger.error(f"所有重试均失败，将跳过此 Feed。")

        # 如果所有重试都失败了，response 仍然是 None，则跳过此 Feed 的后续处理
        if not response:
            continue

        try:
            # 使用 feedparser 解析获取到的 RSS 内容
            logger.info(f"  📡 Feed 响应长度: {len(response.content)} bytes")
            feed = feedparser.parse(response.content)
            logger.info(f"  📡 feed.entries 数量: {len(feed.entries)}")
            if not feed.entries:
                logger.warning(f"  ⚠️ Feed 返回了 0 个条目。Feed URL: {url[:60]}...")
                continue # 如果 Feed 中没有条目，则跳过

            # 倒序遍历 Feed 中的条目，通常较新的条目在前面，倒序处理可以更符合时间顺序
            for entry in reversed(feed.entries):
                entry_title = entry.get('title', '')
                torrent_url = None

                # 优先从 <enclosure> 标签中获取 .torrent 文件的直接链接
                if hasattr(entry, 'enclosures') and entry.enclosures:
                    for enclosure in entry.enclosures:
                        if 'application/x-bittorrent' in enclosure.get('type', ''):
                            torrent_url = enclosure.href
                            break
                # 如果没有找到 <enclosure>，则使用 <link> 标签作为备用方案
                if not torrent_url:
                    torrent_url = entry.get('link')
                if not torrent_url:
                    continue # 如果还是没有链接，则跳过此条目

                # --- 核心过滤逻辑 ---
                # 1. 检查条目标题是否包含所有"必须包含"的关键词 (不区分大小写)
                is_include_match = all(k.lower() in entry_title.lower() for k in include_keywords) if include_keywords else True

                # 2. 检查条目标题是否包含任何"必须不含"的关键词 (不区分大小写)
                is_exclude_match = any(k.lower() in entry_title.lower() for k in exclude_keywords) if exclude_keywords else False

                # 如果满足"必须包含"且不满足"必须不含"的条件，则判定为需要下载
                if is_include_match and not is_exclude_match:
                    # 检查这个种子链接是否已经存在于我们的历史记录中
                    if torrent_url in known_urls:
                        logger.info(f"  -> ⏭️  已存在历史记录，跳过: {entry_title}")
                        continue
                    
                    logger.info(f"发现新项目: {entry_title} -> [规则匹配成功]")
                    try:
                        # Mikan 的 .torrent 下载需要登录 Cookie，qB 无法直接获取，
                        # 所以先通过剧集页面提取 magnet link
                        magnet_url = get_magnet_from_torrent_url(torrent_url, logger)
                        if not magnet_url:
                            logger.error(f"  -> ❌ 无法获取 magnet link，跳过此条目")
                            continue

                        # qB 的 torrents_add 可能返回空 added_torrent_ids
                        # （种子已存在 / 响应丢失 / 请求异常），
                        # 统一用 info-hash 反向确认 qBit 实际状态，
                        # 避免 history 与 qBit 不一致导致丢集无法补回。
                        info_hash = extract_info_hash_from_magnet(magnet_url)
                        qbit_auth = (qbit_user, qbit_pass) if qbit_user and qbit_pass else None
                        qbit_url = f"http://{qbit_host}:{qbit_port}/api/v2/torrents/add"
                        try:
                            body = cffi_requests.post(qbit_url, auth=qbit_auth, data={
                                'urls': magnet_url,
                                'category': qbit_category,
                                'savepath': save_path.rstrip('/'),
                            }).json()
                            added_ids = body.get('added_torrent_ids') or []
                        except Exception as e:
                            logger.warning(f"  -> ⚠️ 添加请求响应异常: {e}，将反向确认 qBit 实际状态")
                            body = {}
                            added_ids = []

                        confirmed = bool(added_ids)
                        if not confirmed and info_hash:
                            try:
                                existing = qbt_client.torrents_info(hashes=info_hash)
                                confirmed = bool(existing)
                                if confirmed:
                                    logger.info(f"  -> ✅ 反向确认：qBit 已存在该种子 (hash={info_hash[:10]}...)，补记历史")
                            except Exception as e:
                                logger.warning(f"  -> ⚠️ 反向确认 qBit 失败: {e}")

                        if confirmed:
                            logger.info(f"  -> ✅ 成功添加到 qBittorrent，分类为 '{qbit_category}'。路径为 '{save_path}'")
                        else:
                            logger.error(f"  -> ❌ qB 未接受该种子 (added={len(added_ids)}, pending={body.get('pending_count', '?')})")
                            continue

                        # 创建新的历史记录对象，包含 URL、分类名，以及从标题解析的集号
                        ep_num = extract_episode_number(entry_title)
                        new_history_item = {
                            "url": torrent_url,
                            "title": qbit_category,
                            "episodes": [ep_num] if ep_num else []
                        }
                        if new_history_item not in downloaded_history_list:
                            downloaded_history_list.append(new_history_item)
                        # 实时更新 URL 集合，防止在同一轮次中重复添加来自不同源的同一文件
                        if torrent_url not in known_urls:
                            known_urls.add(torrent_url)

                        new_downloads_this_run += 1

                        # --- 发送通知 ---
                        if notify_config and notify_config.get('enabled', False):
                            feed_title = feed_item.get('title', '未知番组')
                            notify_title = f"📥 下载完成: {feed_title}"
                            notify_message = (
                                f"**番组:** {feed_title}\n"
                                f"**集数:** {entry_title}\n"
                                f"**分类:** {qbit_category}\n"
                                f"**保存路径:** {save_path}"
                            )
                            send_notification(notify_title, notify_message, notify_config, logger)
                    except Exception as e:
                        logger.error(f"  -> ❌ 添加到 qBittorrent 失败: {e}")
        except Exception as e:
            logger.error(f"解析 Feed 或添加任务时发生内部错误: {e}")

        # 每个 Feed 处理完毕后，等待1秒，避免对服务器造成过大压力
        time.sleep(1)

    # 在所有 Feed 都处理完毕后，如果本轮有新下载，则将更新后的历史记录写回文件
    if new_downloads_this_run > 0:
        save_history(downloaded_history_list)

    logger.info("--- 所有 RSS Feed 检查完成 ---")
    return True


# ── 缺集检测 ──────────────────────────────────────────

def get_downloaded_episodes(feed_title: str) -> set[int]:
    """
    从 downloaded_history 中获取某番剧已下载的所有集号。
    同时兼容新旧数据格式。
    """
    history = load_history()
    downloaded: set[int] = set()
    for item in history:
        # 匹配 classification 后的标题
        if item.get('title') == feed_title:
            eps = item.get('episodes', [])
            if eps:
                downloaded.update(eps)
    return downloaded


def get_qbit_episodes(feed_title: str, qbit_config: dict | None = None) -> tuple[set[int], bool]:
    """
    从 qBittorrent 实际种子中获取某番剧已下载/正在下载的集号。
    返回 (集号集合, qBit 是否可用)：
      - qBit 可用且分类下无种子 → (set(), True)，表示真的没有（全部可补回）
      - qBit 不可用（未配置/连接失败）→ (set(), False)，调用方降级为只读 history
    """
    if not qbit_config or not qbit_config.get('host'):
        return set(), False
    try:
        qbt_client = Client(
            host=qbit_config.get('host'),
            port=int(qbit_config.get('port')) if qbit_config.get('port') else 9888,
            username=qbit_config.get('username'),
            password=qbit_config.get('password'),
            VERIFY_WEBUI_CERTIFICATE=False,
            REQUESTS_ARGS={'timeout': (5, 15)},
        )
        qbt_client.auth_log_in()
        cat_name, _ = clean_category_name(feed_title)
        torrents = qbt_client.torrents_info(category=cat_name)
        eps: set[int] = set()
        for t in torrents:
            ep = extract_episode_number(t.name)
            if ep:
                eps.add(ep)
        return eps, True
    except Exception:
        return set(), False


def get_rss_episodes(feed_url: str, proxy_config: dict | None = None, logger=None) -> dict[int, dict]:
    """
    获取当前 RSS feed 中所有条目及其集号。
    返回 {ep_number: {url, title, torrent_url}, ...}
    """
    result: dict[int, dict] = {}
    proxies = proxy_config if proxy_config and proxy_config.get('http') else None
    try:
        resp = cffi_requests.get(feed_url, impersonate="chrome110", timeout=10, proxies=proxies)
        if resp.status_code != 200:
            return result
        feed = feedparser.parse(resp.content)
        for entry in feed.entries:
            title = entry.get('title', '')
            ep = extract_episode_number(title)
            if ep is None:
                continue
            torrent_url = None
            if hasattr(entry, 'enclosures') and entry.enclosures:
                for enc in entry.enclosures:
                    if 'application/x-bittorrent' in enc.get('type', ''):
                        torrent_url = enc.href
                        break
            if not torrent_url:
                torrent_url = entry.get('link')
            if torrent_url:
                result[ep] = {
                    'url': torrent_url,
                    'title': title,
                }
    except Exception as e:
        if logger:
            logger.error(f"获取 RSS 剧集失败: {feed_url}: {e}")
        else:
            print(f"[ERROR] 获取 RSS 剧集失败: {feed_url}: {e}")
    return result


def detect_missing_episodes(
    feed_item: dict,
    proxy_config: dict | None = None,
    logger=None,
    qbit_config: dict | None = None,
) -> dict | None:
    """
    检测单个 feed 的缺集情况。
    返回:
    {
      "total_episodes": 12,
      "missing": [
        {"ep": 3, "in_rss": true, "torrent_url": "..."},
        {"ep": 5, "in_rss": false, "torrent_url": null}
      ],
      "bangumi_subject_id": 12345,
      "bangumi_name_cn": "我心里危险的东西",
      "episode_info": {3: {"title": "xxx"}, ...}  # RSS 中可获取的集的标题信息
    }
    或 None (Bangumi 未匹配到)
    """
    from bangumi_api import get_subject, get_episodes, search_subjects

    feed_title = feed_item.get('title', '')
    bangumi_id = feed_item.get('bangumi_subject_id')

    # ── 1. 如果没有 bangumi_id，尝试自动匹配 ──
    if not bangumi_id:
        search_results = search_subjects(feed_title)
        if not search_results:
            if logger:
                logger.warning(f"缺集检测: 无法自动匹配 Bangumi ID: {feed_title}")
            return None
        bangumi_id = search_results[0]['subject_id']
        # 注意：调用方负责将 bangumi_id 写回配置

    # ── 2. 获取番剧信息 ──
    subject = get_subject(bangumi_id)
    if not subject:
        return None
    total_episodes = subject.get('total_episodes', 0) or subject.get('eps', 0)
    if total_episodes == 0:
        if logger:
            logger.info(f"缺集检测: Bangumi 尚无剧集数据 (subject_id={bangumi_id})")
        # 尝试从剧集列表获取
        bangumi_eps = get_episodes(bangumi_id)
        if bangumi_eps:
            total_episodes = max(e['ep'] for e in bangumi_eps)
    if total_episodes == 0:
        return {
            'total_episodes': 0,
            'missing': [],
            'bangumi_subject_id': bangumi_id,
            'bangumi_name_cn': subject.get('name_cn', ''),
            'episode_info': {},
        }

    # ── 3. 获取已下载集号 ──
    # 以 qBit 实际种子为准（qBit 里没有的集 = 缺，可补回）；
    # qBit 不可用（未配置/连接失败）时降级为只读 history，避免误报。
    cat_name, _ = clean_category_name(feed_title)
    qbit_eps, qbit_available = get_qbit_episodes(feed_title, qbit_config)
    if qbit_available:
        downloaded_eps = qbit_eps
    else:
        downloaded_eps = get_downloaded_episodes(cat_name)

    # ── 4. 获取 RSS 当前可用集 ──
    feed_url = feed_item.get('url', '')
    rss_eps = get_rss_episodes(feed_url, proxy_config, logger)
    rss_ep_numbers: set[int] = set(rss_eps.keys())

    # ── 5. 计算缺集 ──
    expected_range = set(range(1, total_episodes + 1))
    missing_ep_numbers = expected_range - downloaded_eps

    # ── 6. 构建结果 ──
    episode_info = {}
    missing = []
    for ep in sorted(missing_ep_numbers):
        in_rss = ep in rss_ep_numbers
        entry = {
            'ep': ep,
            'in_rss': in_rss,
            'torrent_url': rss_eps[ep]['url'] if in_rss else None,
        }
        missing.append(entry)
        if in_rss:
            episode_info[ep] = rss_eps[ep]

    return {
        'total_episodes': total_episodes,
        'missing': missing,
        'bangumi_subject_id': bangumi_id,
        'bangumi_name_cn': subject.get('name_cn', ''),
        'episode_info': episode_info,
    }
