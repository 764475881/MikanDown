# MikanDown UI Redesign — Tabler + 海报墙

## Overview

将 MikanDown 前端从 Pico CSS v1 升级到 Tabler CSS 框架，统一视觉风格，引入动漫海报墙背景。

## Pages

### 1. Login (`login.html`)

- Tabler CSS (CDN) + 自定义深色主题
- 全屏海报墙背景（8×4 网格，斜向 -8deg 旋转，缓慢 CSS 滚动）
- 海报来源：用户已订阅番组的海报图片 + 默认占位图
- 磨砂玻璃卡片（`backdrop-filter: blur`），白色半透明输入框
- 粉色调渐变色强调（`#e64980 → #f06595`）
- Logo: "MikanDown" + 渐变填充文字

### 2. Setup (`setup.html`)

- 与 login 一致的海报墙背景 + 玻璃卡片
- 简化为仅用户名 + 密码 + 确认密码三个输入框
- 当用户通过 `/wizard` 访问时，由 wizard 步骤 1 承载
- 此页面仅用于直接访问 `/setup` 的降级场景

### 3. Wizard (`wizard.html`)

- 海报墙背景 + 玻璃卡片
- 步骤指示器：编号圆点 + 连接线（已完成/进行中/待处理）
- 步骤 1：设置管理员账户（表单 POST 到 `/setup`）
- 步骤 2：连接 qBittorrent（AJAX 测试 + 保存）
- 步骤 3：添加订阅（可选，带跳过按钮）
- 各步骤左右两个按钮（上一步 / 下一步或完成）

### 4. Index（主控制台 `index.html`）

- **不使用海报墙**，保持纯色深色背景（`#1a1b1e`），避免干扰操作
- 顶部导航栏（Tabler navbar）：Logo + 操作按钮组
- 可折叠侧边栏（Tabler sidebar）：qBittorrent 设置、全局过滤器、代理、手动检查、OPML 导入/导出、登出、实时日志
- 状态栏：4 个统计卡片（订阅数、下载量、活跃种子、磁盘使用）
- 订阅卡片网格：响应式（`grid-template-columns: repeat(auto-fill, minmax(180px, 1fr))`），海报 + 标题 + 字幕组标签 + 删除按钮
- 添加订阅：顶部输入栏 + 模态框确认过滤规则

## Visual Design Tokens

```css
:root {
  --bg-primary: #1a1b1e;
  --bg-card: #232529;
  --bg-glass: rgba(26, 27, 30, 0.55);
  --border-subtle: rgba(255, 255, 255, 0.08);
  --border-input: rgba(255, 255, 255, 0.1);
  --accent: #e64980;
  --accent-gradient: linear-gradient(135deg, #e64980, #f06595);
  --text-primary: #ffffff;
  --text-secondary: #c1c2c5;
  --text-muted: #868e96;
  --font-family: 'Inter', system-ui, -apple-system, sans-serif;
  --glass-blur: blur(16px);
  --bg-blur: blur(8px);
  --bg-brightness: brightness(0.25);
  --card-radius: 12px;
  --input-radius: 8px;
}
```

## Background Poster Wall

实现方案：

- 构建一个 8 列 × N 行的海报网格
- 整层包裹在 `transform: rotate(-8deg) scale(1.6); filter: blur(8px) brightness(0.25);` 中
- 内层双层海报集（A 组 + B 组），通过 CSS `@keyframes scrollBg` 向上缓慢滚动（40s 循环）
- 仅用于 login / setup / wizard 页面，index 页面不使用

### 海报来源策略

- **Login 页面**（未登录）：使用 8 种不同的静态彩色渐变方块作为占位（每种颜色对应一个番组风格）
- **Wizard/Setup 页面**（已登录）：如果 `config.feeds` 中有订阅项，使用其 `cover_url` + `title` 渲染海报墙；如已订阅少于 6 个，用彩色渐变补满
- 海报图片加载失败时 `onerror` → 替换为彩色渐变占位

### 具体 HTML 结构

```html
<!-- 背景层 -->
<div class="poster-wall" style="position:fixed;inset:0;overflow:hidden;z-index:-1;">
  <div class="poster-wall-scroll">
    <div class="poster-wall-grid">
      <!-- Jinja2 渲染：遍历 feeds + 默认占位生成 24+ 个卡片 -->
      {% for feed in feeds %}
        <img src="https://mikanani.me{{ feed.cover_url }}" alt="">
      {% endfor %}
    </div>
    <!-- 重复一份实现无缝滚动 -->
    <div class="poster-wall-grid"><!-- 同上 --></div>
  </div>
</div>
```

- `poster-wall`: `transform: rotate(-8deg) scale(1.6); filter: blur(8px) brightness(0.25);`
- `poster-wall-scroll`: `animation: scrollBg 40s linear infinite;`
- `poster-wall-grid`: `display: grid; grid-template-columns: repeat(8, 1fr); gap: 4px;`
- 每个海报项：`aspect-ratio: 2/3; border-radius: 4px;`

## CSS Animation

```css
@keyframes scrollBg {
  0% { transform: translateY(0); }
  100% { transform: translateY(-50%); }
}

@media (prefers-reduced-motion: reduce) {
  .poster-wall-scroll { animation: none; }
}
```

## Framework Integration

通过 CDN 引入 Tabler，不修改项目构建工具链：

```html
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@tabler/core@1.4.0/dist/css/tabler.min.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3/dist/tabler-icons.min.css">
<script src="https://cdn.jsdelivr.net/npm/@tabler/core@1.4.0/dist/js/tabler.min.js" defer></script>
```

自定义覆盖样式写入 `<style>` 块（保持简单，不增加额外文件）。

## 不涉及

- 不修改 Python 后端路由/API（仅更新 HTML+CSS+JS）
- 不引入前端构建工具（Vite/Webpack）
- 不修改页面功能逻辑（仅视觉更新）
- 不改动数据模型或配置结构
