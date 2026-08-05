import json
import logging
import re
import threading
import time
from urllib.parse import urlparse, parse_qs
import os
import shutil
import time as time_module
import hashlib
from functools import wraps
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
from flask import Flask, Response, render_template, request, jsonify, session, redirect, url_for, flash
from flask_apscheduler import APScheduler

from backend_script import (process_all_feeds, load_history, save_history, clean_category_name, detect_missing_episodes)
from bangumi_api import (search_subjects, get_subject, get_calendar,
                          get_mikan_season_list,
                          load_season_ratings, save_season_ratings,
                          rating_needs_fetch, fetch_subject_rating)
from curl_cffi import requests as cffi_requests
import feedparser
from qbittorrentapi import Client

class Config: SCHEDULER_API_ENABLED = True
app = Flask(__name__)
app.config.from_object(Config())
app.secret_key = 'a_very_secret_and_random_key_for_sessions_replace_me'
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
os.makedirs(DATA_DIR, exist_ok=True)
CONFIG_FILE = os.path.join(DATA_DIR, 'config.json')
LOG_FILE = os.path.join(DATA_DIR, 'script.log')
last_update_time = None
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[logging.FileHandler(LOG_FILE, mode='a', encoding='utf-8'), logging.StreamHandler()])
logger = logging.getLogger(__name__)

# 缺集检测缓存（内存）
_missing_cache: dict = {}
_missing_cache_time: float = 0

def load_config():
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"feeds": [], "proxy": {}, "filters": {}, "qbit": {}, "auth": {}}
def save_config(config):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f: json.dump(config, f, indent=4, ensure_ascii=False)

@app.before_request
def check_for_setup():
    config = load_config()
    password_is_set = config.get('auth') and config['auth'].get('password_hash')
    qbit_is_set = config.get('qbit') and config['qbit'].get('host')
    has_feeds = len(config.get('feeds', [])) > 0
    # 如果 auth 和 qbit 都设置了，向导即可视为完成；订阅可稍后添加
    wizard_complete = password_is_set and qbit_is_set
    if not wizard_complete and request.endpoint not in ['wizard', 'setup', 'static', 'login', 'api_test_qbit', 'preview_feed', 'api_status', 'update_qbit_settings', 'add_feed', 'update_global_filters', 'update_proxy', 'delete_feed', 'bangumi_search', 'bangumi_set', 'feeds_missing', 'feed_missing', 'add_single_torrent', 'api_season', 'season_subscribe', 'season_preview_groups', 'api_add_status']:
        return redirect(url_for('wizard'))
    if wizard_complete and request.endpoint == 'wizard':
        return redirect(url_for('index'))

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        password_confirm = request.form.get('password_confirm', '').strip()
        if not username or not password:
            flash('用户名或密码不能为空。')
            return render_template('setup.html')
        if password != password_confirm:
            flash('两次输入的密码不一致。')
            return render_template('setup.html')
        salt = os.urandom(16)
        password_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
        config = load_config()
        config['auth'] = { 'username': username, 'salt': salt.hex(), 'password_hash': password_hash.hex() }
        save_config(config)
        flash('管理员账户创建成功！')
        session['logged_in'] = True
        return redirect(url_for('wizard'))
    return render_template('setup.html')

@app.route('/wizard', methods=['GET'])
def wizard():
    return render_template('wizard.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        config = load_config()
        auth_config = config.get('auth')
        username = request.form.get('username')
        password = request.form.get('password')
        salt = bytes.fromhex(auth_config['salt'])
        correct_hash = bytes.fromhex(auth_config['password_hash'])
        password_hash_to_check = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
        if username == auth_config['username'] and password_hash_to_check == correct_hash:
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            flash('用户名或密码错误')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    config = load_config()
    return render_template('index.html', config=config)

@app.route('/api/preview_feed', methods=['POST'])
@login_required
def preview_feed():
    rss_url = request.form.get('rss_url'); config = load_config(); proxies_to_use = config.get('proxy') if config.get('proxy', {}).get('http') else None
    try:
        response_rss = cffi_requests.get(rss_url, impersonate="chrome110", proxies=proxies_to_use, timeout=30); response_rss.raise_for_status(); feed = feedparser.parse(response_rss.content); channel_title = feed.feed.title.replace("Mikan Project - ", "").strip()
        return jsonify({"success": True, "title": channel_title})
    except Exception as e: return jsonify({"success": False, "message": str(e)}), 500
@app.route('/add', methods=['POST'])
@login_required
def add_feed():
    data = request.json; config = load_config(); rss_url = data.get('url')
    if not rss_url:
        return jsonify({"success": False, "message": "缺少 RSS URL"}), 400
    if any(feed['url'] == rss_url for feed in config['feeds']):
        return jsonify({"success": False, "message": "Feed已存在"}), 409

    new_feed_object = {
        "url": rss_url,
        "title": data.get('title') or rss_url,
        "cover_url": "",
        "filters": data.get('filters') or {},
        "subgroup": (data.get('subgroup') or '').strip(),
    }
    config['feeds'].append(new_feed_object)
    save_config(config)
    # feeds 结构变化：使缺集检测缓存失效，避免新 feed 因旧缓存(按旧 index)拿不到结果
    global _missing_cache
    _missing_cache.clear()

    with _add_tasks_lock:
        _add_tasks[rss_url] = {'status': 'processing', 'message': '正在解析字幕组、获取海报...', 'meta': {}}

    def _background_add():
        """后台补充元数据（字幕组/海报）并立即触发下载，避免阻塞添加请求"""
        proxies_to_use = config.get('proxy') if config.get('proxy', {}).get('http') else None
        status, message = 'done', '已开始自动下载'
        try:
            # 1) 解析 RSS 提取字幕组（若用户未指定）
            if not new_feed_object.get('subgroup'):
                try:
                    response_rss = cffi_requests.get(rss_url, impersonate="chrome110", proxies=proxies_to_use, timeout=30)
                    response_rss.raise_for_status()
                    feed = feedparser.parse(response_rss.content)
                    if feed.entries:
                        match = re.search(r"^[\[【]([^\]】]+)[\]】]", feed.entries[0].title)
                        if match:
                            new_feed_object['subgroup'] = match.group(1).strip()
                except Exception as e:
                    logger.error(f"添加 Feed 后解析 RSS 失败 '{rss_url}': {e}")

            # 2) 获取海报
            parsed_url = urlparse(rss_url)
            query_params = parse_qs(parsed_url.query)
            bangumi_id = query_params.get('bangumiId', [None])[0]
            if bangumi_id:
                try:
                    bangumi_page_url = f"https://mikanani.me/Home/Bangumi/{bangumi_id}"
                    response_html = cffi_requests.get(bangumi_page_url, impersonate="chrome110", proxies=proxies_to_use, timeout=30)
                    soup = BeautifulSoup(response_html.content, 'lxml')
                    poster_div = soup.find('div', class_='bangumi-poster')
                    if poster_div and 'style' in poster_div.attrs:
                        style_match = re.search(r"url\('(.+?)'\)", poster_div['style'])
                        if style_match:
                            new_feed_object['cover_url'] = style_match.group(1)
                except Exception as e:
                    logger.error(f"获取海报失败 '{rss_url}': {e}")
        except Exception as e:
            logger.error(f"添加 Feed 后台处理异常 '{rss_url}': {e}")
        finally:
            save_config(config)

        # 3) 立即触发下载（新线程中运行，仅处理这个 feed）——失败必须告知前端
        try:
            from backend_script import process_all_feeds
            ok = process_all_feeds([new_feed_object], config.get('proxy', {}), config.get('qbit', {}), logger, notify_config=config.get('notify', {}))
            if ok:
                status, message = 'done', '已开始自动下载'
            else:
                status, message = 'failed', '无法连接 qBittorrent 或处理出错，请检查设置'
        except Exception as e:
            logger.error(f"添加后自动下载失败 '{rss_url}': {e}")
            status, message = 'failed', f'自动下载失败: {e}'
        finally:
            with _add_tasks_lock:
                _add_tasks[rss_url] = {'status': status, 'message': message,
                                       'meta': {'subgroup': new_feed_object.get('subgroup', ''),
                                                'cover_url': new_feed_object.get('cover_url', '')},
                                       'ts': time.time()}

    threading.Thread(target=_background_add, daemon=True).start()
    return jsonify({"success": True, "config": config})
@app.route('/delete/<int:feed_id>')
@login_required
def delete_feed(feed_id):
    should_delete_files = request.args.get('delete_files', 'false').lower() == 'true'; config = load_config(); qbit_config = config.get('qbit', {})
    if 0 <= feed_id < len(config['feeds']):
        feed_to_delete = config['feeds'].pop(feed_id)
        feed_title = feed_to_delete.get('title', '')
        # 使用与 backend_script 相同的分类名清洗逻辑
        qbit_category_to_delete, _ = clean_category_name(feed_title)

        if should_delete_files and qbit_category_to_delete:
            try:
                qbt_client = _build_qbit_client(qbit_config); qbt_client.auth_log_in(); torrents = qbt_client.torrents_info(category=qbit_category_to_delete)
                if torrents: qbt_client.torrents_delete(delete_files=True, torrent_hashes=[t.hash for t in torrents])
                qbt_client.torrents_remove_categories(categories=qbit_category_to_delete); history_list = load_history(); updated_history = [item for item in history_list if item.get('title') != qbit_category_to_delete]
                if len(history_list) != len(updated_history): save_history(updated_history)
            except Exception as e: logger.error(f"连接 qBittorrent 或删除时出错: {e}")
        save_config(config)
        # feeds 结构变化：使缺集检测缓存失效（index 已偏移）
        global _missing_cache, _season_cache, _season_cache_time
        _missing_cache.clear()
        _season_cache = None
        _season_cache_time = 0
        return jsonify({"success": True, "config": config})
    return jsonify({"success": False, "message": "无效的Feed ID"}), 404
@app.route('/update_proxy', methods=['POST'])
@login_required
def update_proxy():
    global _season_cache, _season_cache_time
    config = load_config(); http_proxy = request.form.get('http_proxy', '').strip(); config['proxy']['http'] = http_proxy; config['proxy']['https'] = http_proxy; save_config(config)
    # 代理变更后清除季节缓存，使下次请求重新获取
    _season_cache = None; _season_cache_time = 0
    return jsonify({"success": True})
@app.route('/update_global_filters', methods=['POST'])
@login_required
def update_global_filters():
    config = load_config();
    if 'filters' not in config: config['filters'] = {}
    config['filters']['include'] = request.form.get('include_keywords', '').strip(); config['filters']['exclude'] = request.form.get('exclude_keywords', '').strip(); save_config(config)
    return jsonify({"success": True})
@app.route('/update_qbit_settings', methods=['POST'])
@login_required
def update_qbit_settings():
    config = load_config();
    if 'qbit' not in config: config['qbit'] = {}
    config['qbit']['host'] = request.form.get('qbit_host'); config['qbit']['port'] = int(request.form.get('qbit_port') or 9888); config['qbit']['username'] = request.form.get('qbit_username'); config['qbit']['password'] = request.form.get('qbit_password'); config['qbit']['save_path_base'] = request.form.get('qbit_save_path'); save_config(config)
    return jsonify({"success": True})

def _build_qbit_client(qbit_config):
    """构建 qBittorrent 客户端，自动处理 port 类型转换"""
    return Client(
        host=qbit_config.get('host'),
        port=int(qbit_config.get('port', 9888)),
        username=qbit_config.get('username'),
        password=qbit_config.get('password'),
        VERIFY_WEBUI_CERTIFICATE=False,
        REQUESTS_ARGS={'timeout': (10, 30)}
    )

@app.route('/api/test_qbit', methods=['POST'])
@login_required
def api_test_qbit():
    data = request.json
    try:
        qbt_client = Client(
            host=data.get('host'),
            port=int(data.get('port', 9888)),
            username=data.get('username'),
            password=data.get('password'),
            VERIFY_WEBUI_CERTIFICATE=False,
            REQUESTS_ARGS={'timeout': (10, 30)}
        )
        qbt_client.auth_log_in()
        version = qbt_client.app.version
        return jsonify({"success": True, "version": version})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})
@app.route('/run')
@login_required
def run_script():
    config = load_config(); thread = threading.Thread(target=process_all_feeds, args=(config['feeds'], config['proxy'], config.get('qbit',{}), logger)); thread.start()
    return jsonify({"success": True, "message": "任务已在后台启动"})

@app.route('/api/status')
def api_status():
    config = load_config()
    feed_count = len(config.get('feeds', []))
    downloaded_total = 0
    active_torrents = 0
    disk_usage = None
    try:
        qbit_config = config.get('qbit', {})
        if qbit_config.get('host'):
            qbt_client = _build_qbit_client(qbit_config)
            qbt_client.auth_log_in()
            all_torrents = qbt_client.torrents_info()
            # 下载中：downloading/queuedDL/stalledDL/metaDL/forcedDL（含元数据获取）
            active_torrents = len([t for t in all_torrents if t.state in ('downloading', 'queuedDL', 'stalledDL', 'metaDL', 'forcedDL')])
            # 已完成：completed + 各种做种状态（uploading/stalledUP/queuedUP/pausedUP/forcedUP）
            downloaded_total = len([t for t in all_torrents if t.state in ('completed', 'uploading', 'stalledUP', 'queuedUP', 'pausedUP', 'forcedUP')])
    except Exception:
        active_torrents = -1
        downloaded_total = -1
    try:
        disk_total, disk_used, disk_free = shutil.disk_usage(DATA_DIR)
        disk_usage = {"total": round(disk_total / (1024**3), 1), "used": round(disk_used / (1024**3), 1), "free": round(disk_free / (1024**3), 1), "unit": "GB"}
    except Exception:
        pass
    return jsonify({"feed_count": feed_count, "downloaded_total": downloaded_total, "active_torrents": active_torrents, "last_update": last_update_time, "disk_usage": disk_usage, "auth_set": bool(config.get('auth') and config['auth'].get('password_hash')), "qbit_set": bool(config.get('qbit') and config['qbit'].get('host'))})

@app.route('/api/feeds/export')
@login_required
def export_opml():
    config = load_config()
    opml = ET.Element('opml', version='2.0')
    head = ET.SubElement(opml, 'head')
    title = ET.SubElement(head, 'title')
    title.text = 'MikanDown 订阅列表'
    body = ET.SubElement(opml, 'body')
    for feed in config.get('feeds', []):
        outline = ET.SubElement(body, 'outline')
        outline.set('text', feed.get('title', ''))
        outline.set('type', 'rss')
        outline.set('xmlUrl', feed.get('url', ''))
        outline.set('htmlUrl', '')
        filters = feed.get('filters', {})
        if filters.get('include'):
            outline.set('mikan_include_filter', filters['include'])
        if filters.get('exclude'):
            outline.set('mikan_exclude_filter', filters['exclude'])
    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(opml, encoding='unicode')
    return Response(xml_str, mimetype='text/xml', headers={'Content-Disposition': 'attachment; filename=mikandown_subscriptions.opml'})

@app.route('/api/feeds/import', methods=['POST'])
@login_required
def import_opml():
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "请上传文件"}), 400
    file = request.files['file']
    if not file.filename.endswith('.opml') and not file.filename.endswith('.xml'):
        return jsonify({"success": False, "message": "请上传 OPML/XML 文件"}), 400
    try:
        tree = ET.parse(file)
        root = tree.getroot()
        config = load_config()
        imported_count = 0
        for outline in root.iter('outline'):
            xml_url = outline.get('xmlUrl')
            if not xml_url:
                continue
            if any(feed['url'] == xml_url for feed in config['feeds']):
                continue
            new_feed = {
                "url": xml_url,
                "title": outline.get('text', outline.get('title', '')),
                "cover_url": "",
                "filters": {
                    "include": outline.get('mikan_include_filter', ''),
                    "exclude": outline.get('mikan_exclude_filter', '')
                },
                "subgroup": ""
            }
            config['feeds'].append(new_feed)
            imported_count += 1
        save_config(config)
        return jsonify({"success": True, "imported": imported_count, "total": len(config['feeds'])})
    except ET.ParseError as e:
        return jsonify({"success": False, "message": f"OPML 解析失败: {e}"}), 400


# ── Bangumi 搜索 ──────────────────────────────────────

@app.route('/api/feeds/bangumi-search', methods=['POST'])
@login_required
def bangumi_search():
    """搜索 Bangumi 番剧"""
    data = request.json
    keyword = (data.get('keyword') or '').strip()
    if not keyword:
        return jsonify({"success": False, "message": "请输入关键词"}), 400
    results = search_subjects(keyword)
    return jsonify({"success": True, "results": results})


# ── 设置 Bangumi ID ───────────────────────────────────

@app.route('/api/feeds/<int:feed_id>/bangumi-set', methods=['POST'])
@login_required
def bangumi_set(feed_id):
    """手动为某个 feed 设置 bangumi_subject_id"""
    config = load_config()
    if feed_id < 0 or feed_id >= len(config['feeds']):
        return jsonify({"success": False, "message": "无效的 Feed ID"}), 404

    data = request.json
    subject_id = data.get('bangumi_subject_id')
    if not subject_id:
        return jsonify({"success": False, "message": "缺少 bangumi_subject_id"}), 400

    # 验证 subject_id 是否有效
    subject = get_subject(int(subject_id))
    if not subject:
        return jsonify({"success": False, "message": "无法获取该 Bangumi ID 的信息"}), 400

    feed = config['feeds'][feed_id]
    feed['bangumi_subject_id'] = int(subject_id)
    feed['bangumi_name_cn'] = subject.get('name_cn', '')
    save_config(config)

    return jsonify({"success": True, "subject": subject})


# ── 缺集检测（所有 feed） ─────────────────────────────

@app.route('/api/feeds/missing')
@login_required
def feeds_missing():
    """检查所有 feed 的缺集情况（5分钟缓存，?refresh=1 强制重新检测）"""
    global _missing_cache, _missing_cache_time
    config = load_config()

    feeds = config.get('feeds', [])
    if not feeds:
        return jsonify({"success": True, "feeds": []})

    force_refresh = request.args.get('refresh') == '1'

    # 检查是否已缓存且未过期（5分钟）
    now = time.time()
    if not force_refresh and _missing_cache and (now - _missing_cache_time) < 300:
        # 注意：必须转回 list 再返回，前端期望数组；直接返回 dict 会让前端 for...of 抛错
        return jsonify({"success": True, "feeds": list(_missing_cache.values()), "cached": True})

    proxy = config.get('proxy', {})
    qbit = config.get('qbit', {})
    results = []
    updated_config = False

    from concurrent.futures import ThreadPoolExecutor, as_completed

    # 并行检测所有 feed 的缺集情况（网络请求是主要耗时，串行会放大延迟）
    def _detect_one(feed):
        try:
            return detect_missing_episodes(feed, proxy, logger, qbit)
        except Exception as e:
            logger.error(f"缺集检测异常 '{feed.get('title','')}': {e}")
            return None

    detected: list[dict | None] = [None] * len(feeds)
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(feeds)))) as pool:
        futures = {pool.submit(_detect_one, feed): idx for idx, feed in enumerate(feeds)}
        for future in as_completed(futures):
            idx = futures[future]
            try:
                detected[idx] = future.result()
            except Exception as e:
                logger.error(f"缺集检测线程异常: {e}")
                detected[idx] = None

    for idx, (feed, result) in enumerate(zip(feeds, detected)):
        if result is None and not feed.get('bangumi_subject_id'):
            # 自动匹配失败 — 跳过，不修改配置
            results.append({"feed_id": idx, "matched": False, "missing": []})
            continue

        if result:
            # 如果自动匹配到了新的 bangumi_id，写回配置
            if not feed.get('bangumi_subject_id') and result.get('bangumi_subject_id'):
                feed['bangumi_subject_id'] = result['bangumi_subject_id']
                feed['bangumi_name_cn'] = result.get('bangumi_name_cn', '')
                updated_config = True
            results.append({
                "feed_id": idx,
                "matched": True,
                "total_episodes": result.get('total_episodes', 0),
                "missing": result.get('missing', []),
                "downloaded": result.get('downloaded', []),
                "downloading": result.get('downloading', []),
                "bangumi_subject_id": result.get('bangumi_subject_id'),
                "bangumi_name_cn": result.get('bangumi_name_cn', ''),
            })
        else:
            results.append({"feed_id": idx, "matched": False, "missing": []})

    if updated_config:
        save_config(config)

    # 写缓存。注意：全缺结果（downloaded 为空且 missing 非空）不缓存——
    # 刚订阅时 qBit 种子可能尚未添加完成，此时检测到的"全缺"是瞬时状态，
    # 若写入 5 分钟缓存会让页面一直显示全缺；跳过缓存后下次请求自动重检。
    _missing_cache.clear()
    for r in results:
        if r.get('matched') and not r.get('downloaded') and r.get('missing'):
            continue
        _missing_cache[r['feed_id']] = r
    _missing_cache_time = now

    return jsonify({"success": True, "feeds": results, "cached": False})


# ── 缺集检测（单个 feed） ─────────────────────────────

@app.route('/api/feeds/<int:feed_id>/missing')
@login_required
def feed_missing(feed_id):
    """检查单个 feed 的缺集"""
    config = load_config()
    if feed_id < 0 or feed_id >= len(config.get('feeds', [])):
        return jsonify({"success": False, "message": "无效的 Feed ID"}), 404

    feed = config['feeds'][feed_id]
    proxy = config.get('proxy', {})
    result = detect_missing_episodes(feed, proxy, logger, config.get('qbit', {}))

    if result is None and not feed.get('bangumi_subject_id'):
        return jsonify({
            "success": False,
            "matched": False,
            "message": "未设置 Bangumi ID 且自动搜索无结果，请手动设置"
        })

    # 自动匹配到新 ID 则保存
    if not feed.get('bangumi_subject_id') and result and result.get('bangumi_subject_id'):
        feed['bangumi_subject_id'] = result['bangumi_subject_id']
        feed['bangumi_name_cn'] = result.get('bangumi_name_cn', '')
        save_config(config)

    return jsonify({"success": True, "matched": True, "result": result})


# ── 下载单集 ─────────────────────────────────────────

@app.route('/add_single_torrent', methods=['POST'])
@login_required
def add_single_torrent():
    """下载单集种子到 qBittorrent"""
    data = request.json
    feed_id = data.get('feed_id')
    torrent_url = data.get('torrent_url')
    ep = data.get('ep')

    if not torrent_url or feed_id is None:
        return jsonify({"success": False, "message": "参数不完整"}), 400

    config = load_config()
    if feed_id < 0 or feed_id >= len(config.get('feeds', [])):
        return jsonify({"success": False, "message": "无效的 Feed ID"}), 404

    feed = config['feeds'][feed_id]
    qbit_config = config.get('qbit', {})
    category, _ = clean_category_name(feed.get('title', ''))
    save_path_base = qbit_config.get('save_path_base', '/downloads/')
    save_path = f"{save_path_base}{category}/"

    try:
        qbt_client = _build_qbit_client(qbit_config)
        qbt_client.auth_log_in()
        qbt_client.torrents_add(urls=torrent_url, category=category, save_path=save_path)

        # 记录到下载历史
        history = load_history()
        history.append({
            "url": torrent_url,
            "title": category,
            "episodes": [ep] if ep else []
        })
        save_history(history)

        return jsonify({"success": True, "message": f"已添加第{ep}集到下载队列"})
    except Exception as e:
        logger.error(f"添加单集下载失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


_season_cache: list | None = None
_season_cache_time: float = 0
_SEASON_CACHE_TTL = 600  # 10 分钟 → 生产环境建议 600

# 后台添加任务状态：rss_url -> {status: processing|done|failed, message, meta}
_add_tasks: dict[str, dict] = {}
_add_tasks_lock = threading.Lock()

@app.route('/api/add_status')
@login_required
def api_add_status():
    """查询后台添加任务状态（前端轮询，保证添加流程完整可见）"""
    url = request.args.get('url', '')
    if not url:
        return jsonify({'status': 'not_found'}), 400
    with _add_tasks_lock:
        task = _add_tasks.get(url)
        if task is None:
            # 顺带清理过期的终态任务，避免字典无限增长
            now = time.time()
            expired = [k for k, v in _add_tasks.items()
                       if v.get('status') in ('done', 'failed') and now - v.get('ts', 0) > 1800]
            for k in expired:
                _add_tasks.pop(k, None)
    if not task:
        return jsonify({'status': 'not_found'})
    return jsonify(task)

@app.route('/api/season')
@login_required
def api_season():
    """当季番 — 纯 Mikan 首页数据（快），不再请求 Bangumi 日历/搜索。
    评分从 season_ratings 缓存实时注入；无分/过期条目触发后台补分线程。"""
    global _season_cache, _season_cache_time
    now = time.time()

    force_refresh = request.args.get('refresh') == '1'
    if force_refresh:
        from bangumi_api import invalidate_cache
        invalidate_cache('mikan_homepage')
        _season_cache = None
        _season_cache_time = 0

    if not force_refresh and _season_cache and (now - _season_cache_time) < _SEASON_CACHE_TTL:
        result = _season_cache
    else:
        config = load_config()
        proxy = config.get('proxy') if config.get('proxy', {}).get('http') else None
        subscribed_feeds = config.get('feeds', [])

        def _same_bangumi_url(url_a: str, url_b: str) -> bool:
            """判断两个 RSS URL 是否指向同一部番剧：
            完全相等，或 bangumiId 相同（字幕组专属 RSS 带 &subgroupid=，与番剧级 RSS 是同一部番）。"""
            if url_a == url_b:
                return True
            def _bangumi_id(u: str):
                return parse_qs(urlparse(u).query).get('bangumiId', [None])[0]
            a, b = _bangumi_id(url_a), _bangumi_id(url_b)
            return bool(a and a == b)

        # 唯一数据源：Mikan 首页（当季新番，按星期几/剧场版分组，含海报 + 最新更新日期）
        mikan_items = get_mikan_season_list(proxy=proxy)

        result: list = []
        by_weekday: dict[int, list] = {}
        noid_counter = 0
        for mikan in mikan_items:
            if mikan.get('has_resource', True):
                sid = mikan['bangumi_id']
            else:
                noid_counter += 1
                sid = f"noid-{noid_counter}"   # 无资源番剧无 bangumi_id，用自增标识
            entry = {
                'subject_id': sid,             # 唯一，用作前端定位
                'name': '',
                'name_cn': mikan['title'],
                'image': mikan.get('mikan_poster_url', ''),
                'mikan_poster': mikan.get('mikan_poster_url', ''),
                'summary': '',
                'rating': 0,                   # 占位，返回前注入实时评分
                'air_weekday': mikan.get('weekday', 0),   # 1=周一 ... 7=周日, 0=剧场版
                'last_update': mikan.get('last_update', ''),   # 如 "2026/07/28"
                'mikan_rss_url': mikan['rss_url'],
                'has_resource': mikan.get('has_resource', True),
                'is_subscribed': any(_same_bangumi_url(mikan['rss_url'], f.get('url', '')) for f in subscribed_feeds),
            }
            by_weekday.setdefault(mikan.get('weekday', 0), []).append(entry)

        # 按标准顺序分组输出：周一~周日 + 剧场版(0)，与 Mikan 首页星期分类一致
        for wd in (1, 2, 3, 4, 5, 6, 7, 0):
            entries = by_weekday.get(wd)
            if entries:
                result.append({'__separator__': True, 'weekday': wd})
                result.extend(entries)

        has_data = any('__separator__' not in item for item in result)
        if has_data:
            _season_cache = result
            _season_cache_time = now

    # 注入实时评分（不写进 season 缓存，评分更新后下次请求立即生效）
    ratings = load_season_ratings()
    need_fetch_ids: list[tuple[int, str]] = []
    response_data = []
    for item in result:
        if '__separator__' in item:
            response_data.append(item)
            continue
        entry = dict(item)
        sid = entry['subject_id']
        if isinstance(sid, int):
            r = ratings.get(str(sid))
            entry['rating'] = (r or {}).get('score', 0)
            if rating_needs_fetch(ratings, sid):
                need_fetch_ids.append((sid, entry.get('name_cn') or ''))
        response_data.append(entry)

    # 有需要补分的番剧 → 后台线程慢慢补（每部间隔限速，不阻塞页面）
    if need_fetch_ids:
        _ensure_rating_background(need_fetch_ids)

    return jsonify(response_data)


# ── 当季番评分后台补分 ────────────────────────────────
_RATING_QUEUE_LOCK = threading.Lock()
_rating_worker_running = False


def _ensure_rating_background(need_fetch: list[tuple[int, str]]) -> None:
    """启动后台评分补分线程（单例）。need_fetch: [(bangumi_id, name), ...]"""
    global _rating_worker_running
    if not need_fetch:
        return
    with _RATING_QUEUE_LOCK:
        if _rating_worker_running:
            return
        _rating_worker_running = True
    t = threading.Thread(target=_rating_worker, args=(need_fetch,), daemon=True)
    t.start()


def _rating_worker(need_fetch: list[tuple[int, str]]) -> None:
    """后台线程：逐个抓取评分，间隔 1.5s 限速（Bangumi API 友好），慢慢补。"""
    global _rating_worker_running
    try:
        config = load_config()
        proxy = config.get('proxy') if config.get('proxy', {}).get('http') else None
        logger.info(f"[评分后台] 开始补分: {len(need_fetch)} 部")
        for idx, (sid, name) in enumerate(need_fetch):
            fetch_subject_rating(sid, name, proxy)
            if idx < len(need_fetch) - 1:
                time.sleep(1.5)
        logger.info("[评分后台] 补分完成")
    except Exception as e:
        logger.error(f"[评分后台] 补分异常: {e}")
    finally:
        with _RATING_QUEUE_LOCK:
            _rating_worker_running = False


def _fetch_subgroup_rss_map(bangumi_id, proxies_to_use):
    """抓取 Mikan 番剧详情页，返回 {字幕组名: 该字幕组专属 RSS URL}。
    每个字幕组的 RSS 只含本组条目（/RSS/Bangumi?bangumiId=X&subgroupid=Y）。
    注意：详情页区块名（如 Kirara Fantasia）可能和 RSS 条目前缀（如 [黒ネズミたち]）不一致，
    因此对每个 subgroup 抓取专属 RSS，以条目的实际前缀作为组名 key，保证与 preview_groups 分组一致。"""
    result = {}
    try:
        page_url = f"https://mikanani.me/Home/Bangumi/{bangumi_id}"
        resp = cffi_requests.get(page_url, impersonate="chrome110", proxies=proxies_to_use, timeout=30)
        if resp.status_code != 200:
            return result
        soup = BeautifulSoup(resp.content, 'lxml')
        # 详情页每个字幕组区块: <div class="subgroup-name subgroup-{id}">
        subgroup_ids = []
        for el in soup.select('.subgroup-name'):
            cls = ' '.join(el.get('class', []))
            m = re.search(r'subgroup-(\d+)', cls)
            if m:
                subgroup_ids.append(m.group(1))
        # 对每个 subgroup 抓专属 RSS，取条目前缀作为组名
        for sid in subgroup_ids:
            sub_rss_url = (
                f"https://mikanani.me/RSS/Bangumi?bangumiId={bangumi_id}&subgroupid={sid}"
            )
            try:
                r2 = cffi_requests.get(sub_rss_url, impersonate="chrome110", proxies=proxies_to_use, timeout=20)
                if r2.status_code != 200:
                    continue
                sub_feed = feedparser.parse(r2.content)
                for entry in sub_feed.entries[:5]:
                    t = entry.get('title', '')
                    m2 = re.match(r'^[\[【]([^\]】]+)[\]】]', t)
                    if m2:
                        gname = m2.group(1).strip()
                        if gname and gname not in result:
                            result[gname] = sub_rss_url
                        break
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"获取字幕组 RSS 映射失败: {e}")
    return result


@app.route('/api/season/preview_groups', methods=['POST'])
@login_required
def season_preview_groups():
    """解析 Bangumi RSS，按字幕组分组返回资源列表（名称 + 大小 + 各字幕组专属 RSS URL）"""
    data = request.json
    rss_url = data.get('rss_url')
    if not rss_url:
        return jsonify({"success": False, "message": "缺少 RSS URL"}), 400

    config = load_config()
    proxies_to_use = config.get('proxy') if config.get('proxy', {}).get('http') else None

    # 字幕组专属 RSS 映射：{组名: 专属 RSS URL}
    subgroup_rss_map = {}
    try:
        qp = parse_qs(urlparse(rss_url).query)
        bangumi_id = qp.get('bangumiId', [None])[0]
        if bangumi_id:
            subgroup_rss_map = _fetch_subgroup_rss_map(bangumi_id, proxies_to_use)
    except Exception:
        subgroup_rss_map = {}

    try:
        resp = cffi_requests.get(rss_url, impersonate="chrome110", proxies=proxies_to_use, timeout=30)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)

        groups: dict[str, dict] = {}   # name -> {count, items}
        uncategorized = []
        for entry in feed.entries:
            title = entry.get('title', '')
            # 资源大小：优先 enclosure.length，其次 contentlength
            size = 0
            if hasattr(entry, 'enclosures') and entry.enclosures:
                for enc in entry.enclosures:
                    if enc.get('type', '') == 'application/x-bittorrent' or enc.get('href', '').endswith('.torrent'):
                        try:
                            size = int(enc.get('length') or 0)
                        except (TypeError, ValueError):
                            size = 0
                        break
            if not size:
                try:
                    size = int(entry.get('contentlength') or 0)
                except (TypeError, ValueError):
                    size = 0
            item = {"title": title, "size": size}

            match = re.match(r'^[\[【]([^\]】]+)[\]】]', title)
            if match:
                group = match.group(1).strip()
                g = groups.setdefault(group, {"count": 0, "items": []})
                g["count"] += 1
                g["items"].append(item)
            else:
                uncategorized.append(item)

        # 未匹配到字幕组前缀的条目归入"其他"
        if uncategorized:
            groups.setdefault("其他", {"count": len(uncategorized), "items": uncategorized})

        # 按条目数降序排列
        sorted_groups = sorted(groups.items(), key=lambda x: -x[1]["count"])
        group_list = [
            {
                "name": name,
                "count": g["count"],
                "items": g["items"],
                # 每个字幕组专属 RSS URL（只含该组条目，无需 include 字符串过滤）
                "subgroup_rss_url": subgroup_rss_map.get(name, ''),
            }
            for name, g in sorted_groups
        ]

        return jsonify({
            "success": True,
            "groups": group_list,
            "single_group": group_list[0]["name"] if len(group_list) == 1 else None
        })
    except Exception as e:
        logger.error(f"预览字幕组失败 '{rss_url}': {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/api/season/subscribe', methods=['POST'])
@login_required
def season_subscribe():
    """订阅一部当季番剧并立即下载所有已有剧集"""
    data = request.json
    rss_url = data.get('rss_url')
    title = data.get('title', '未知番剧')
    subgroup = data.get('subgroup', '').strip()
    mikan_poster = data.get('mikan_poster', '').strip()
    include = (data.get('include') or '').strip()   # 用户自定义包含关键词
    exclude = (data.get('exclude') or '').strip()   # 用户自定义排除关键词

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
            "cover_url": mikan_poster,  # 前端传的 Mikan 海报兜底
            "filters": {},
            "subgroup": subgroup
        }

        # include 过滤器 = [字幕组名] + 用户自定义包含关键词（全部匹配才下载）
        include_parts = []
        # 字幕组专属 RSS（含 subgroupid）已只含本组条目，无需再加字幕组 include 关键词；
        # 番剧级 RSS 才需要靠 [字幕组] 关键词过滤
        is_subgroup_rss = 'subgroupid' in rss_url
        if subgroup and not is_subgroup_rss:
            include_parts.append(f"[{subgroup}]")
        for kw in include.split():
            if kw not in include_parts:
                include_parts.append(kw)
        if include_parts:
            new_feed['filters']['include'] = ' '.join(include_parts)
        # exclude 过滤器 = 用户自定义排除关键词（任一匹配即跳过）
        if exclude:
            new_feed['filters']['exclude'] = exclude

        # 如果没指定字幕组，自动从 RSS 首个条目提取
        if not subgroup:
            response_rss = cffi_requests.get(rss_url, impersonate="chrome110", proxies=proxies_to_use, timeout=30)
            response_rss.raise_for_status()
            feed = feedparser.parse(response_rss.content)
            if feed.entries:
                match = re.search(r"^[\[【]([^\]】]+)[\]】]", feed.entries[0].title)
                if match:
                    new_feed['subgroup'] = match.group(1).strip()
                    if 'include' in new_feed['filters']:
                        new_feed['filters']['include'] = f"[{match.group(1).strip()}] " + new_feed['filters']['include']

        # 如果没有 Mikan 海报（前端订阅或手动输入），再从 Mikan Bangumi 页面获取
        if not new_feed['cover_url']:
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
        # feeds 结构变化：使缺集检测缓存失效
        global _missing_cache
        _missing_cache.clear()

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

        return jsonify({"success": True, "message": "已订阅并开始下载", "config": config})
    except Exception as e:
        logger.error(f"订阅当季番失败 '{rss_url}': {e}")
        return jsonify({"success": False, "message": f"订阅失败: {e}"}), 500


@app.route('/log')
@login_required
def stream_log():
    def generate():
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            f.seek(0, 2)
            while True:
                line = f.readline()
                if not line: time.sleep(0.5); continue
                yield f"data: {line.strip()}\n\n"
    return Response(generate(), mimetype='text/event-stream')
scheduler = APScheduler()
@scheduler.task('interval', id='rss_check_job', minutes=30, misfire_grace_time=900)
def scheduled_task():
    global last_update_time; last_update_time = time_module.strftime('%Y-%m-%dT%H:%M:%S')
    with app.app_context():
        logger.info("--- [APScheduler] 定时任务已启动 ---")
        config = load_config()
        if config and config.get('feeds'):
            process_all_feeds(config['feeds'], config['proxy'], config.get('qbit',{}), logger)
        else:
            logger.warning("[APScheduler] 配置文件未找到或没有 Feed。")
        logger.info("--- [APScheduler] 定时任务执行完毕 ---")

@scheduler.task('cron', id='season_rating_daily', hour=3, minute=30, misfire_grace_time=3600)
def daily_season_rating_refresh():
    """每天凌晨 3:30 全量刷新当季番评分（Bangumi 评分通常日更）。"""
    with app.app_context():
        logger.info("--- [APScheduler] 每日评分刷新开始 ---")
        try:
            config = load_config()
            proxy = config.get('proxy') if config.get('proxy', {}).get('http') else None
            mikan_items = get_mikan_season_list(proxy=proxy)
            need = [(m['bangumi_id'], m.get('title', '')) for m in mikan_items
                    if m.get('has_resource') and m.get('bangumi_id')]
            logger.info(f"[每日评分] 待刷新 {len(need)} 部")
            if need:
                _ensure_rating_background(need)
        except Exception as e:
            logger.error(f"[每日评分] 刷新异常: {e}")
        logger.info("--- [APScheduler] 每日评分刷新结束 ---")

if __name__ == '__main__':
    scheduler.init_app(app)
    scheduler.start()
    app.run(host='0.0.0.0', port=5000, debug=False)