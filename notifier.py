"""
MikanDown 通知系统
支持: Telegram / Discord / PushPlus
"""
import json
import logging
import requests


def send_notification(title: str, message: str, notify_config: dict, logger: logging.Logger):
    """
    发送下载完成通知。
    
    Args:
        title: 通知标题
        message: 通知正文
        notify_config: 通知配置字典
        logger: 日志记录器
    """
    if not notify_config or not notify_config.get('enabled', False):
        return

    _senders = {
        'telegram': _send_telegram,
        'discord': _send_discord,
        'pushplus': _send_pushplus,
    }

    channel = notify_config.get('channel', '')
    sender_func = _senders.get(channel)
    if not sender_func:
        logger.warning(f"[通知系统] 未知的通知渠道: {channel}")
        return

    try:
        sender_func(title, message, notify_config, logger)
    except Exception as e:
        logger.error(f"[通知系统] 发送通知失败 ({channel}): {e}")


# ── Telegram ──────────────────────────────────────────────────

def _send_telegram(title: str, message: str, config: dict, logger: logging.Logger):
    bot_token = config.get('bot_token', '').strip()
    chat_id = config.get('chat_id', '').strip()
    if not bot_token or not chat_id:
        logger.warning("[Telegram] bot_token 或 chat_id 未配置")
        return

    text = f"*{title}*\n{message}"
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'Markdown',
        'disable_web_page_preview': True,
    }
    resp = requests.post(url, json=payload, timeout=15)
    if resp.status_code != 200:
        logger.error(f"[Telegram] 发送失败: {resp.text}")
    else:
        logger.info("[Telegram] 通知发送成功")


# ── Discord ───────────────────────────────────────────────────

def _send_discord(title: str, message: str, config: dict, logger: logging.Logger):
    webhook_url = config.get('webhook_url', '').strip()
    if not webhook_url:
        logger.warning("[Discord] webhook_url 未配置")
        return

    payload = {
        'embeds': [{
            'title': title,
            'description': message,
            'color': 0x00bfff,
        }]
    }
    resp = requests.post(webhook_url, json=payload, timeout=15)
    if resp.status_code not in (200, 204):
        logger.error(f"[Discord] 发送失败: {resp.text}")
    else:
        logger.info("[Discord] 通知发送成功")


# ── PushPlus ──────────────────────────────────────────────────

def _send_pushplus(title: str, message: str, config: dict, logger: logging.Logger):
    token = config.get('token', '').strip()
    if not token:
        logger.warning("[PushPlus] token 未配置")
        return

    url = "https://www.pushplus.plus/send"
    payload = {
        'token': token,
        'title': title,
        'content': message,
        'template': 'txt',
    }
    resp = requests.post(url, json=payload, timeout=15)
    if resp.status_code != 200:
        logger.error(f"[PushPlus] 发送失败: {resp.text}")
    else:
        logger.info("[PushPlus] 通知发送成功")
