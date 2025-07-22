import json
import logging
import re
import threading
import time
from urllib.parse import urlparse, parse_qs
import os
import hashlib
from functools import wraps

from bs4 import BeautifulSoup
from flask import Flask, Response, render_template, request, jsonify, session, redirect, url_for, flash
from flask_apscheduler import APScheduler

from backend_script import (process_all_feeds, load_history, save_history)
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
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[logging.FileHandler(LOG_FILE, mode='a', encoding='utf-8'), logging.StreamHandler()])
logger = logging.getLogger(__name__)

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
    if not password_is_set and request.endpoint not in ['setup', 'static']:
        return redirect(url_for('setup'))
    if password_is_set and request.endpoint == 'setup':
        return redirect(url_for('login'))

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
        flash('管理员账户创建成功！现在请登录。')
        return redirect(url_for('login'))
    return render_template('setup.html')

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
    if any(feed['url'] == rss_url for feed in config['feeds']): return jsonify({"success": False, "message": "Feed已存在"}), 409
    try:
        proxies_to_use = config.get('proxy') if config.get('proxy', {}).get('http') else None
        new_feed_object = { "url": rss_url, "title": data.get('title'), "cover_url": "", "filters": data.get('filters'), "subgroup": "" }
        response_rss = cffi_requests.get(rss_url, impersonate="chrome110", proxies=proxies_to_use, timeout=30); response_rss.raise_for_status(); feed = feedparser.parse(response_rss.content)
        if feed.entries:
            match = re.search(r"^[\[【]([^\]】]+)[\]】]", feed.entries[0].title)
            if match: new_feed_object['subgroup'] = match.group(1).strip()
        parsed_url = urlparse(rss_url); query_params = parse_qs(parsed_url.query); bangumi_id = query_params.get('bangumiId', [None])[0]
        if bangumi_id:
            bangumi_page_url = f"https://mikanani.me/Home/Bangumi/{bangumi_id}"; response_html = cffi_requests.get(bangumi_page_url, impersonate="chrome110", proxies=proxies_to_use, timeout=30); soup = BeautifulSoup(response_html.content, 'lxml'); poster_div = soup.find('div', class_='bangumi-poster')
            if poster_div and 'style' in poster_div.attrs:
                style_match = re.search(r"url\('(.+?)'\)", poster_div['style'])
                if style_match: new_feed_object['cover_url'] = style_match.group(1)
        config['feeds'].append(new_feed_object); save_config(config)
        return jsonify({"success": True, "config": config})
    except Exception as e:
        logger.error(f"添加 Feed '{rss_url}' 时发生错误: {e}")
        return jsonify({"success": False, "message": f"获取附加信息失败，错误: {e}"}), 500
@app.route('/delete/<int:feed_id>')
@login_required
def delete_feed(feed_id):
    should_delete_files = request.args.get('delete_files', 'false').lower() == 'true'; config = load_config(); qbit_config = config.get('qbit', {})
    if 0 <= feed_id < len(config['feeds']):
        feed_to_delete = config['feeds'].pop(feed_id); feed_title = feed_to_delete.get('title'); subgroup = feed_to_delete.get('subgroup', ''); qbit_category_to_delete = f"{feed_title}_{subgroup}" if subgroup else feed_title
        if should_delete_files and qbit_category_to_delete:
            try:
                qbt_client = Client(host=qbit_config.get('host'), port=qbit_config.get('port'), username=qbit_config.get('username'), password=qbit_config.get('password')); qbt_client.auth_log_in(); torrents = qbt_client.torrents_info(category=qbit_category_to_delete)
                if torrents: qbt_client.torrents_delete(delete_files=True, torrent_hashes=[t.hash for t in torrents])
                qbt_client.torrents_remove_categories(categories=qbit_category_to_delete); history_list = load_history(); updated_history = [item for item in history_list if item.get('title') != qbit_category_to_delete]
                if len(history_list) != len(updated_history): save_history(updated_history)
            except Exception as e: logger.error(f"连接 qBittorrent 或删除时出错: {e}")
        save_config(config)
        return jsonify({"success": True, "config": config})
    return jsonify({"success": False, "message": "无效的Feed ID"}), 404
@app.route('/update_proxy', methods=['POST'])
@login_required
def update_proxy():
    config = load_config(); http_proxy = request.form.get('http_proxy', '').strip(); config['proxy']['http'] = http_proxy; config['proxy']['https'] = http_proxy; save_config(config)
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
    config['qbit']['host'] = request.form.get('qbit_host'); config['qbit']['port'] = request.form.get('qbit_port'); config['qbit']['username'] = request.form.get('qbit_username'); config['qbit']['password'] = request.form.get('qbit_password'); config['qbit']['save_path_base'] = request.form.get('qbit_save_path'); save_config(config)
    return jsonify({"success": True})
@app.route('/run')
@login_required
def run_script():
    config = load_config(); thread = threading.Thread(target=process_all_feeds, args=(config['feeds'], config['proxy'], config.get('qbit',{}), logger)); thread.start()
    return jsonify({"success": True, "message": "任务已在后台启动"})
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
    with app.app_context():
        logger.info("--- [APScheduler] 定时任务已启动 ---")
        config = load_config()
        if config and config.get('feeds'):
            process_all_feeds(config['feeds'], config['proxy'], config.get('qbit',{}), logger)
        else:
            logger.warning("[APScheduler] 配置文件未找到或没有 Feed。")
        logger.info("--- [APScheduler] 定时任务执行完毕 ---")

if __name__ == '__main__':
    scheduler.init_app(app)
    scheduler.start()
    app.run(host='0.0.0.0', port=5000, debug=False)