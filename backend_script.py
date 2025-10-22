# --- 1. 导入必要的库 ---
import re

import feedparser  # 用于解析 RSS 和 Atom fee
# d
import json  # 用于处理 JSON 数据 (历史记录文件)
import time  # 用于在循环中添加延迟
from qbittorrentapi import Client  # qBittorrent 的 API 客户端库
from curl_cffi import requests as cffi_requests  # 模拟浏览器的网络请求库，用于绕过网站防火墙
import os
# --- 2. 全局常量 ---

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 定义历史记录文件的路径，方便统一管理
DATA_DIR = os.path.join(BASE_DIR, 'data')
HISTORY_FILE = os.path.join(DATA_DIR, 'downloaded_history.json')

# --- 3. 辅助函数 (读写历史记录) ---
print(HISTORY_FILE)
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
        # 如果中文数字不在我们的映射中（例如“十一”），则返回 Season 1
        return "Season 1"

# --- 4. 核心处理函数 ---
def process_all_feeds(feed_objects, proxy_config, qbit_config, logger):
    """
    处理所有给定的 Feed 对象列表，检查更新并添加到 qBittorrent。
    这个函数是整个后台下载逻辑的核心，被主应用 `main.py` 在手动或定时任务中调用。

    :param feed_objects: 包含每个订阅详细信息的对象列表 (url, title, filters 等)。
    :param proxy_config: 包含代理服务器设置的字典。
    :param qbit_config: 包含 qBittorrent 连接信息的字典。
    :param logger: 从主应用传入的日志记录器实例。
    """
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
        qbt_client = Client(host=qbit_host, port=qbit_port, username=qbit_user, password=qbit_pass)
        qbt_client.auth_log_in()
        logger.info("✅ 成功连接到 qBittorrent。")
    except Exception as e:
        # 如果连接失败，记录错误并中止本次任务
        logger.error(f"❌ 连接 qBittorrent 失败: {e} (请检查侧边栏中的 qBittorrent 设置)")
        return

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

        # 根据番剧标题和字幕组名称，构造在 qBittorrent 中唯一的分类名
        qbit_category = f"{feed_title}_{subgroup}" if subgroup else feed_title

        session = get_season_string(qbit_category)

        regex = re.compile(r"(第\s*[一二三四五六七八九十\d]+\s*季)")
        found = regex.search(qbit_category)
        if found:
            qbit_category = qbit_category.replace(found[0], '')
            qbit_category = qbit_category.strip()
            print(f"检测到为 {qbit_category} 的第 {session}")
        # 构造唯一的保存路径
        save_path = f"{download_path_base}{qbit_category}/{session}/"

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
            feed = feedparser.parse(response.content)
            if not feed.entries:
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

                # 检查这个种子链接是否已经存在于我们的历史记录中
                # if torrent_url not in known_urls:
                if True:
                    # --- 核心过滤逻辑 ---
                    # 1. 检查条目标题是否包含所有“必须包含”的关键词 (不区分大小写)
                    is_include_match = all(k.lower() in entry_title.lower() for k in include_keywords) if include_keywords else True

                    # 2. 检查条目标题是否包含任何“必须不含”的关键词 (不区分大小写)
                    is_exclude_match = any(k.lower() in entry_title.lower() for k in exclude_keywords) if exclude_keywords else False

                    # 如果满足“必须包含”且不满足“必须不含”的条件，则判定为需要下载
                    if is_include_match and not is_exclude_match:
                        logger.info(f"发现新项目: {entry_title} -> [规则匹配成功]")
                        try:
                            # 调用 qBittorrent API 添加下载任务
                            qbt_client.torrents_add(urls=torrent_url, category=qbit_category, save_path=save_path)
                            logger.info(f"  -> ✅ 成功添加到 qBittorrent，分类为 '{qbit_category}'。路径为 '{save_path}'")

                            # 创建新的历史记录对象，包含 URL 和唯一的分类名
                            new_history_item = {"url": torrent_url, "title": qbit_category}
                            downloaded_history_list.append(new_history_item)
                            # 实时更新 URL 集合，防止在同一轮次中重复添加来自不同源的同一文件
                            if torrent_url not in known_urls:
                                known_urls.add(torrent_url)

                            new_downloads_this_run += 1
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