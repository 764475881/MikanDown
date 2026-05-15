# MikanDown 体验打磨实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 subagent-driven-development（推荐）或 executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在现有 Flask + JSON 存储架构上，实现配置向导、状态仪表盘、OPML 导入/导出三个功能，提升新用户上手体验和日常使用便利性。

**架构：** 所有后端逻辑在 `main.py` 中新增路由和辅助函数；前端在 `templates/` 中新增/修改 HTML 文件；全部零新 Python 依赖。

**技术栈：** Flask, Pico CSS, Python 标准库 (xml.etree, shutil, json)

---

### 任务 1：状态仪表盘 — 后端 API

**文件：**
- 修改：`/opt/git_clone/MikanDown/main.py`（新增第 25 行附近导入 shutil，第 77-96 行新增 /api/status 路由，第 20 行附近新增 last_update_time 变量）

- [ ] **步骤 1：添加 shutil 导入和 last_update_time 变量**

在 `main.py` 顶部 `import os` 之后添加：

```python
import shutil
import time as time_module
```

在 `CONFIG_FILE` 和 `LOG_FILE` 定义之后（约第 27 行）添加全局变量：

```python
last_update_time = None
```

- [ ] **步骤 2：在调度器中更新 last_update_time**

找到 `scheduled_task()` 函数（约第 193 行），在函数体内第一行添加：

```python
global last_update_time; last_update_time = time_module.strftime('%Y-%m-%dT%H:%M:%S')
```

- [ ] **步骤 3：新增 /api/status 路由**

在 `run_script` 路由（约第 165 行）之后添加：

```python
@app.route('/api/status')
@login_required
def api_status():
    config = load_config()
    feed_count = len(config.get('feeds', []))
    history_list = load_history()
    downloaded_total = len(history_list)
    active_torrents = 0
    disk_usage = None
    try:
        qbit_config = config.get('qbit', {})
        if qbit_config.get('host'):
            qbt_client = Client(host=qbit_config.get('host'), port=qbit_config.get('port'), username=qbit_config.get('username'), password=qbit_config.get('password'))
            qbt_client.auth_log_in()
            all_torrents = qbt_client.torrents_info()
            active_torrents = len([t for t in all_torrents if t.state in ('downloading', 'queuedDL', 'stalledDL')])
    except Exception:
        active_torrents = -1  # -1 表示连接失败
    try:
        disk_total, disk_used, disk_free = shutil.disk_usage(DATA_DIR)
        disk_usage = {"total": round(disk_total / (1024**3), 1), "used": round(disk_used / (1024**3), 1), "free": round(disk_free / (1024**3), 1), "unit": "GB"}
    except Exception:
        pass
    return jsonify({"feed_count": feed_count, "downloaded_total": downloaded_total, "active_torrents": active_torrents, "last_update": last_update_time, "disk_usage": disk_usage})
```

### 任务 2：状态仪表盘 — 前端 UI

**文件：**
- 修改：`/opt/git_clone/MikanDown/templates/index.html`（在页面顶部 page-header 之后、feeds-grid 之前添加状态栏，以及在底部或 Head 中添加 CSS）

- [ ] **步骤 1：添加状态栏 CSS**

在 `index.html` 的 `<style>` 标签内（约第 26 行 `--spacing-vertical` 之后）添加：

```css
.status-bar { display: flex; flex-wrap: wrap; gap: 0.8rem 1.5rem; padding: 0.6rem 0 1.2rem 0; font-size: 0.9rem; border-bottom: 1px solid var(--card-border); margin-bottom: 1.2rem; }
.status-item { display: inline-flex; align-items: center; gap: 0.3rem; }
.status-item .label { color: var(--muted-color); }
.status-item .value { font-weight: 600; }
.status-error { color: var(--del-color); }
```

- [ ] **步骤 2：添加状态栏 HTML**

在 `<div class="page-header">` 之后、`<div class="feeds-grid">` 之前添加：

```html
<div class="status-bar" id="status-bar">
  <div class="status-item"><span class="label">📡 订阅</span><span class="value" id="stat-feeds">-</span></div>
  <div class="status-item"><span class="label">📥 已下载</span><span class="value" id="stat-downloaded">-</span></div>
  <div class="status-item"><span class="label">💾 活跃种子</span><span class="value" id="stat-torrents">-</span></div>
  <div class="status-item"><span class="label">⏱ 上次更新</span><span class="value" id="stat-update">-</span></div>
  <div class="status-item"><span class="label">💿 磁盘</span><span class="value" id="stat-disk">-</span></div>
</div>
```

- [ ] **步骤 3：添加状态加载 JavaScript**

在页面底部 `</body>` 之前，或页内 `<script>` 块中找到 JS 区域，添加：

```html
<script>
fetch('/api/status').then(r=>r.json()).then(d=>{
  document.getElementById('stat-feeds').textContent = d.feed_count;
  document.getElementById('stat-downloaded').textContent = d.downloaded_total;
  document.getElementById('stat-torrents').textContent = d.active_torrents < 0 ? '未连接' : d.active_torrents;
  document.getElementById('stat-update').textContent = d.last_update || '尚未运行';
  if (d.disk_usage) {
    let pct = Math.round(d.disk_usage.used / d.disk_usage.total * 100);
    document.getElementById('stat-disk').textContent = d.disk_usage.free + 'G 空闲 (' + pct + '%)';
  } else {
    document.getElementById('stat-disk').textContent = '-';
  }
}).catch(()=>{});
</script>
```

- [ ] **步骤 4：测试运行**

```bash
cd /opt/git_clone/MikanDown && python main.py &
sleep 3
curl -s http://localhost:5000/api/status | python -m json.tool
```

预期输出（即使 qBittorrent 未连接也不应报 500）：

```json
{
  "feed_count": 12,
  "downloaded_total": 0,
  "active_torrents": -1,
  "last_update": null,
  "disk_usage": {"total": 100.0, "used": 30.0, "free": 70.0, "unit": "GB"}
}
```

- [ ] **步骤 5：Commit**

```bash
cd /opt/git_clone/MikanDown && git add -A && git commit -m "feat: add status dashboard with /api/status endpoint"
```

### 任务 3：配置向导 — 后端

**文件：**
- 创建：`/opt/git_clone/MikanDown/templates/wizard.html`
- 修改：`/opt/git_clone/MikanDown/main.py`（setup 路由 + 新增配置测试 API）

- [ ] **步骤 1：修改 setup 路由判断逻辑**

当前 `check_for_setup` 和 `setup` 路由只处理密码设置。需要修改：`check_for_setup` 中检测 "向导未完成" 的状态，重定向到 `wizard` 而非 `setup`。

定义「向导未完成」的判定条件：
- `config['auth']` 中没有 `password_hash`，或者
- `config['qbit']` 中没有 `host` 和 `port`，或者
- `config['feeds']` 为空列表

在 `check_for_setup()` 函数（约第 37 行）中，修改为：

```python
@app.before_request
def check_for_setup():
    config = load_config()
    password_is_set = config.get('auth') and config['auth'].get('password_hash')
    qbit_is_set = config.get('qbit') and config['qbit'].get('host')
    has_feeds = len(config.get('feeds', [])) > 0
    wizard_complete = password_is_set and qbit_is_set and has_feeds
    if not wizard_complete and request.endpoint not in ['wizard', 'setup', 'static', 'login', 'api_test_qbit', 'api_test_rss']:
        return redirect(url_for('wizard'))
    if wizard_complete and request.endpoint == 'wizard':
        return redirect(url_for('index'))
```

注意：需要同时放开 `setup` 路由的权限（因为 wizard 第 1 步可能调用 setup API 的内部逻辑），以及新增的测试 API。

- [ ] **步骤 2：新增测试 qBittorrent 连接的 API**

在 `update_qbit_settings` 路由之后添加：

```python
@app.route('/api/test_qbit', methods=['POST'])
@login_required
def api_test_qbit():
    data = request.json
    try:
        qbt_client = Client(host=data.get('host'), port=data.get('port'), username=data.get('username'), password=data.get('password'))
        qbt_client.auth_log_in()
        version = qbt_client.app.version()
        return jsonify({"success": True, "version": version})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})
```

- [ ] **步骤 3：新增测试 RSS URL 的 API（已有 `/api/preview_feed` 可复用，无需新增）**

注意：`/api/preview_feed` 是 POST + form-data 格式。在 wizard 中可以直接 POST form 数据来验证 RSS URL。确认已有路由可用即可。

- [ ] **步骤 4：创建向导页面模板 `/opt/git_clone/MikanDown/templates/wizard.html`**

```html
<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark">
<head>
  <meta charset="UTF-8">
  <title>设置向导 - MikanDown</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@1/css/pico.min.css">
  <style>
    main { display: flex; justify-content: center; align-items: center; min-height: 100vh; }
    article { width: 100%; max-width: 520px; }
    .step-indicator { display: flex; justify-content: center; gap: 0.5rem; margin-bottom: 1.5rem; }
    .step-dot { width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 0.8rem; font-weight: 700; background: var(--card-background-color); color: var(--muted-color); }
    .step-dot.active { background: var(--primary); color: var(--primary-inverse); }
    .step-dot.done { background: var(--primary-focus); color: var(--primary); }
    .step-line { width: 40px; height: 2px; align-self: center; background: var(--card-background-color); }
    .step-line.done { background: var(--primary-focus); }
    .step-content { display: none; }
    .step-content.active { display: block; }
    .btn-group { display: flex; justify-content: space-between; margin-top: 1rem; }
    .result-msg { margin-top: 0.5rem; font-size: 0.85rem; }
    .result-msg.success { color: var(--form-element-valid-border-color); }
    .result-msg.error { color: var(--del-color); }
  </style>
</head>
<body>
<main class="container">
  <article>
    <hgroup>
      <h1>欢迎使用 MikanDown 🎉</h1>
      <h2>几步完成初始设置</h2>
    </hgroup>

    <div class="step-indicator">
      <div class="step-dot active" id="sd-1">1</div><div class="step-line" id="sl-1"></div>
      <div class="step-dot" id="sd-2">2</div><div class="step-line" id="sl-2"></div>
      <div class="step-dot" id="sd-3">3</div>
    </div>

    {% with messages = get_flashed_messages() %}
    {% if messages %}
    <p style="color: var(--del-color);">{{ messages[0] }}</p>
    {% endif %}
    {% endwith %}

    <!-- Step 1: 设置密码 -->
    <div class="step-content active" id="step-1">
      <h3>设置管理员账户</h3>
      <p>创建你的管理员用户名和密码。</p>
      <form id="form-step1" method="post" action="{{ url_for('setup') }}">
        <input type="text" name="username" placeholder="管理员用户名" required>
        <input type="password" name="password" placeholder="设置密码" required>
        <input type="password" name="password_confirm" placeholder="确认密码" required>
        <div class="btn-group">
          <span></span>
          <button type="submit">下一步</button>
        </div>
      </form>
    </div>

    <!-- Step 2: 配置 qBittorrent -->
    <div class="step-content" id="step-2">
      <h3>连接 qBittorrent</h3>
      <p>填入 qBittorrent Web UI 的连接信息。</p>
      <form id="form-step2">
        <input type="text" id="qbit_host" placeholder="qBittorrent 地址 (如 192.168.1.100)" required>
        <input type="text" id="qbit_port" placeholder="端口 (默认 8080)" value="8080" required>
        <input type="text" id="qbit_user" placeholder="用户名 (默认 admin)" value="admin">
        <input type="password" id="qbit_pass" placeholder="密码">
        <input type="text" id="qbit_path" placeholder="保存路径 (如 /downloads/)" value="/downloads/">
        <div id="qbit-result" class="result-msg"></div>
        <div class="btn-group">
          <button type="button" class="secondary" onclick="goStep(1)">上一步</button>
          <button type="submit">测试连接并继续</button>
        </div>
      </form>
    </div>

    <!-- Step 3: 添加订阅 -->
    <div class="step-content" id="step-3">
      <h3>添加你的第一个订阅</h3>
      <p>输入 Mikan Project 的 RSS 订阅链接。</p>
      <form id="form-step3">
        <input type="url" id="rss_url" placeholder="RSS URL (右键 MikanProject 复制链接地址)" required>
        <div id="rss-result" class="result-msg"></div>
        <div class="btn-group">
          <button type="button" class="secondary" onclick="goStep(2)">上一步</button>
          <button type="submit">测试并完成</button>
        </div>
      </form>
    </div>

    <p id="wizard-done" style="display:none; text-align:center; color: var(--form-element-valid-border-color);">✅ 设置完成！<a href="{{ url_for('index') }}">进入主界面</a></p>
  </article>
</main>

<script>
let currentStep = 1;
function goStep(n) {
  document.querySelectorAll('.step-content').forEach(el => el.classList.remove('active'));
  document.getElementById('step-' + n).classList.add('active');
  document.querySelectorAll('.step-dot').forEach(el => el.classList.remove('active'));
  document.getElementById('sd-' + n).classList.add('active');
  document.querySelectorAll('.step-line').forEach(el => el.classList.remove('done'));
  for (let i = 1; i < n; i++) {
    document.getElementById('sd-' + i).classList.add('done');
    document.getElementById('sd-' + i).classList.remove('active');
    document.getElementById('sl-' + i).classList.add('done');
  }
  currentStep = n;
}

document.getElementById('form-step1')?.addEventListener('submit', function(e) {
  // 让表单正常提交到 /setup，后台保存密码后重定向到 /wizard 回到 step2
});

document.getElementById('form-step2')?.addEventListener('submit', async function(e) {
  e.preventDefault();
  const btn = this.querySelector('button[type=submit]'); btn.disabled = true; btn.textContent = '测试中...';
  const res = await fetch('/api/test_qbit', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
    host: document.getElementById('qbit_host').value,
    port: document.getElementById('qbit_port').value,
    username: document.getElementById('qbit_user').value,
    password: document.getElementById('qbit_pass').value
  })});
  const data = await res.json();
  const resultEl = document.getElementById('qbit-result');
  if (data.success) {
    // 保存 qBittorrent 配置
    const saveRes = await fetch('/update_qbit_settings', {method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body: new URLSearchParams({
      qbit_host: document.getElementById('qbit_host').value,
      qbit_port: document.getElementById('qbit_port').value,
      qbit_username: document.getElementById('qbit_user').value,
      qbit_password: document.getElementById('qbit_pass').value,
      qbit_save_path: document.getElementById('qbit_path').value
    })});
    const saveData = await saveRes.json();
    if (saveData.success) {
      resultEl.className = 'result-msg success'; resultEl.textContent = '✅ 连接成功 (v' + data.version + ')，已保存配置';
      setTimeout(() => goStep(3), 800);
    }
  } else {
    resultEl.className = 'result-msg error'; resultEl.textContent = '❌ ' + (data.message || '连接失败');
  }
  btn.disabled = false; btn.textContent = '测试连接并继续';
});

document.getElementById('form-step3')?.addEventListener('submit', async function(e) {
  e.preventDefault();
  const btn = this.querySelector('button[type=submit]'); btn.disabled = true; btn.textContent = '验证中...';
  const formData = new FormData(); formData.append('rss_url', document.getElementById('rss_url').value);
  const res = await fetch('/api/preview_feed', {method:'POST', body: formData});
  const data = await res.json();
  const resultEl = document.getElementById('rss-result');
  if (data.success) {
    // 添加订阅
    const addRes = await fetch('/add', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({
      url: document.getElementById('rss_url').value,
      title: data.title,
      filters: {include: '', exclude: ''}
    })});
    const addData = await addRes.json();
    if (addData.success) {
      resultEl.className = 'result-msg success';
      resultEl.textContent = '✅ 已添加: ' + data.title;
      // 隐藏表单，显示完成
      document.getElementById('form-step3').style.display = 'none';
      document.getElementById('wizard-done').style.display = 'block';
    } else {
      resultEl.className = 'result-msg error'; resultEl.textContent = '❌ ' + (addData.message || '添加失败');
    }
  } else {
    resultEl.className = 'result-msg error'; resultEl.textContent = '❌ ' + (data.message || '验证失败');
  }
  btn.disabled = false; btn.textContent = '测试并完成';
});
</script>
</body>
</html>
```

- [ ] **步骤 5：新增 /wizard 路由**

在 `setup` 路由之后添加：

```python
@app.route('/wizard', methods=['GET'])
def wizard():
    return render_template('wizard.html')
```

- [ ] **步骤 6：Commit**

```bash
cd /opt/git_clone/MikanDown && git add -A && git commit -m "feat: add setup wizard with 3-step guided onboarding"
```

### 任务 4：OPML 导入/导出 — 后端

**文件：**
- 修改：`/opt/git_clone/MikanDown/main.py`（新增 `import xml.etree.ElementTree as ET` 导入，新增 `/api/feeds/export` 和 `/api/feeds/import` 路由）

- [ ] **步骤 1：添加 xml.etree 导入**

在 `main.py` 顶部 import 段（约第 8 行）添加：

```python
import xml.etree.ElementTree as ET
```

- [ ] **步骤 2：新增导出 API**

在 `/api/status` 路由之前或之后添加：

```python
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
```

- [ ] **步骤 3：新增导入 API**

```python
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
                "title": outline.get('text', ''),
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
```

- [ ] **步骤 4：Commit**

```bash
cd /opt/git_clone/MikanDown && git add -A && git commit -m "feat: add OPML import/export API"
```

### 任务 5：OPML 导入/导出 — 前端 UI

**文件：**
- 修改：`/opt/git_clone/MikanDown/templates/index.html`（在添加订阅表单附近增加导入/导出按钮）

- [ ] **步骤 1：在添加订阅表单区域增加导入/导出按钮**

找到侧边栏中的 `add-feed-form` 区域，在其下方或旁边（约第 60 行附近）添加：

```html
<div style="display:flex; gap:0.5rem; margin-top:0.5rem;">
  <button class="secondary" style="flex:1; font-size:0.8rem;" onclick="document.getElementById('opml-file-input').click()">📥 导入 OPML</button>
  <a href="/api/feeds/export" class="secondary" style="flex:1; font-size:0.8rem; text-align:center; padding:0.5rem; border-radius:var(--border-radius); border:1px solid var(--form-element-border-color); text-decoration:none;">📤 导出 OPML</a>
</div>
<input type="file" id="opml-file-input" accept=".opml,.xml" style="display:none;" onchange="importOPML(this)">
```

- [ ] **步骤 2：添加导入 JavaScript 处理函数**

在页面现有的 `<script>` 块中添加：

```javascript
async function importOPML(input) {
  if (!input.files.length) return;
  const formData = new FormData();
  formData.append('file', input.files[0]);
  const btn = input.previousElementSibling; btn.textContent = '导入中...'; btn.disabled = true;
  try {
    const res = await fetch('/api/feeds/import', {method:'POST', body: formData});
    const data = await res.json();
    if (data.success) {
      alert('✅ 成功导入 ' + data.imported + ' 个订阅（共 ' + data.total + ' 个）');
      location.reload();
    } else {
      alert('❌ 导入失败: ' + (data.message || '未知错误'));
    }
  } catch(e) {
    alert('❌ 导入失败: ' + e.message);
  }
  btn.textContent = '📥 导入 OPML'; btn.disabled = false;
  input.value = '';
}
```

- [ ] **步骤 3：Commit**

```bash
cd /opt/git_clone/MikanDown && git add -A && git commit -m "feat: add OPML import/export UI buttons"
```

## 完整验证清单

完成后运行以下验证命令：

```bash
cd /opt/git_clone/MikanDown
# 1. Python 语法检查
python -c "import py_compile; py_compile.compile('main.py', doraise=True)" && echo "✅ main.py 语法正确"
python -c "import py_compile; py_compile.compile('backend_script.py', doraise=True)" && echo "✅ backend_script.py 语法正确"

# 2. 启动测试
timeout 5 python main.py 2>&1 | grep -q "Running on" && echo "✅ 应用启动正常" || echo "❌ 启动失败"

# 3. API 端点测试
python main.py &
PID=$!
sleep 3
curl -sf http://localhost:5000/api/status > /dev/null && echo "✅ /api/status 正常"
curl -sf http://localhost:5000/api/feeds/export > /dev/null && echo "✅ /api/feeds/export 正常"
kill $PID 2>/dev/null
```

所有步骤都是"零新依赖"——不修改 `requirements.txt`。
