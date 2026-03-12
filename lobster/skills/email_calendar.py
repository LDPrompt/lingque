"""
🐦 灵雀 - 邮件(IMAP/SMTP) & 飞书日历 & 消息提醒 技能 (P1 完整版)
"""

import json
import asyncio
import email as email_lib
import email.utils
import imaplib
import smtplib
import logging
import os
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
from datetime import datetime, timedelta, timezone

import httpx
from .registry import registry

logger = logging.getLogger("lingque.skills.email_calendar")

# 运行时配置 (由 main.py 注入)
_email_config = {"imap_host": "", "imap_port": 993, "smtp_host": "", "smtp_port": 465, "username": "", "password": ""}
_feishu_config = {"app_id": "", "app_secret": "", "calendar_id": "primary"}

# 提醒任务存储
_reminders_file = Path("./memory/reminders.json")
_reminder_callback = None  # 发送飞书消息的回调函数


def set_reminder_callback(callback):
    """设置提醒回调函数（由 FeishuChannel 注入）"""
    global _reminder_callback
    _reminder_callback = callback


def _load_reminders() -> list:
    """加载待提醒任务"""
    if not _reminders_file.exists():
        return []
    try:
        return json.loads(_reminders_file.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_reminders(reminders: list):
    """保存待提醒任务"""
    _reminders_file.parent.mkdir(parents=True, exist_ok=True)
    _reminders_file.write_text(json.dumps(reminders, ensure_ascii=False, indent=2), encoding="utf-8")


async def check_and_send_reminders():
    """检查并发送到期的提醒（由后台任务调用）"""
    if not _reminder_callback:
        return

    reminders = _load_reminders()
    now = datetime.now()
    remaining = []
    sent_count = 0

    for r in reminders:
        remind_time = datetime.strptime(r["remind_at"], "%Y-%m-%d %H:%M")
        if now >= remind_time:
            try:
                await _reminder_callback(r["chat_id"], f"⏰ **提醒**\n\n{r['title']}\n\n_(设定时间: {r['remind_at']})_")
                sent_count += 1
                logger.info(f"已发送提醒: {r['title']}")
            except Exception as e:
                logger.error(f"发送提醒失败: {e}")
                remaining.append(r)
        else:
            remaining.append(r)

    if sent_count > 0:
        _save_reminders(remaining)

    return sent_count


def configure_email(imap_host, smtp_host, username, password, imap_port=993, smtp_port=465):
    _email_config.update(imap_host=imap_host, imap_port=imap_port, smtp_host=smtp_host,
                         smtp_port=smtp_port, username=username, password=password)


def configure_feishu_calendar(app_id, app_secret, calendar_id="primary"):
    _feishu_config.update(app_id=app_id, app_secret=app_secret, calendar_id=calendar_id)


def _decode_hdr(raw):
    parts = decode_header(raw or "")
    return "".join(p.decode(c or "utf-8", errors="replace") if isinstance(p, bytes) else p for p, c in parts)


def _extract_body(msg):
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                pl = part.get_payload(decode=True)
                if pl:
                    body += pl.decode(part.get_content_charset() or "utf-8", errors="replace")
    else:
        pl = msg.get_payload(decode=True)
        if pl:
            body = pl.decode(msg.get_content_charset() or "utf-8", errors="replace")
    return body[:3000]


# ==================== 邮件技能 ====================

@registry.register(
    name="read_emails",
    description="读取最近的邮件, 返回发件人/主题/时间/正文摘要",
    parameters={
        "type": "object",
        "properties": {
            "count": {"type": "integer", "description": "读取几封", "default": 10},
            "unread_only": {"type": "boolean", "description": "只看未读", "default": False},
            "folder": {"type": "string", "description": "邮箱文件夹", "default": "INBOX"},
        },
    },
    risk_level="low", category="email",
)
async def read_emails(count: int = 10, unread_only: bool = False, folder: str = "INBOX") -> str:
    cfg = _email_config
    if not cfg["imap_host"]:
        return "❌ 邮件未配置! 请在 .env 中设置 EMAIL_IMAP_HOST, EMAIL_USERNAME, EMAIL_PASSWORD"

    def _sync():
        conn = imaplib.IMAP4_SSL(cfg["imap_host"], cfg["imap_port"])
        conn.login(cfg["username"], cfg["password"])
        conn.select(folder)
        _, nums = conn.search(None, "UNSEEN" if unread_only else "ALL")
        ids = nums[0].split()[-count:]
        ids.reverse()
        results = []
        for mid in ids:
            _, data = conn.fetch(mid, "(RFC822)")
            msg = email_lib.message_from_bytes(data[0][1])
            subj = _decode_hdr(msg.get("Subject", "(无主题)"))
            frm = _decode_hdr(msg.get("From", ""))
            date = msg.get("Date", "")
            body = _extract_body(msg)[:200]
            results.append(f"📧 {subj}\n   发件人: {frm}\n   时间: {date}\n   摘要: {body}...")
        conn.logout()
        return results

    try:
        results = await asyncio.get_event_loop().run_in_executor(None, _sync)
        if not results:
            return "邮箱为空" if not unread_only else "没有未读邮件"
        return f"共 {len(results)} 封:\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"读取邮件失败: {e}"


@registry.register(
    name="send_email",
    description="发送邮件",
    parameters={
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "收件人, 多个用逗号分隔"},
            "subject": {"type": "string", "description": "主题"},
            "body": {"type": "string", "description": "正文"},
            "cc": {"type": "string", "description": "抄送", "default": ""},
        },
        "required": ["to", "subject", "body"],
    },
    risk_level="high", category="email",
)
async def send_email(to: str, subject: str, body: str, cc: str = "") -> str:
    cfg = _email_config
    if not cfg["smtp_host"]:
        return "❌ 邮件未配置!"

    def _sync():
        msg = MIMEMultipart()
        msg["From"], msg["To"], msg["Subject"] = cfg["username"], to, subject
        if cc:
            msg["Cc"] = cc
        msg.attach(MIMEText(body, "plain", "utf-8"))
        recipients = [a.strip() for a in to.split(",")] + ([a.strip() for a in cc.split(",")] if cc else [])
        with smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"]) as srv:
            srv.login(cfg["username"], cfg["password"])
            srv.sendmail(cfg["username"], recipients, msg.as_string())

    try:
        await asyncio.get_event_loop().run_in_executor(None, _sync)
        return f"✅ 邮件已发送! 收件人: {to}, 主题: {subject}"
    except Exception as e:
        return f"发送失败: {e}"


@registry.register(
    name="search_emails",
    description="按关键词搜索邮件主题",
    parameters={
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "description": "搜索关键词"},
            "count": {"type": "integer", "description": "最多返回几封", "default": 10},
        },
        "required": ["keyword"],
    },
    risk_level="low", category="email",
)
async def search_emails(keyword: str, count: int = 10) -> str:
    cfg = _email_config
    if not cfg["imap_host"]:
        return "❌ 邮件未配置!"

    def _sync():
        conn = imaplib.IMAP4_SSL(cfg["imap_host"], cfg["imap_port"])
        conn.login(cfg["username"], cfg["password"])
        conn.select("INBOX")
        _, nums = conn.search(None, f'(SUBJECT "{keyword}")')
        ids = nums[0].split()[-count:]
        ids.reverse()
        results = []
        for mid in ids:
            _, data = conn.fetch(mid, "(RFC822)")
            msg = email_lib.message_from_bytes(data[0][1])
            subj = _decode_hdr(msg.get("Subject", ""))
            frm = _decode_hdr(msg.get("From", ""))
            date = msg.get("Date", "")
            results.append(f"📧 {subj}\n   {frm} | {date}")
        conn.logout()
        return results

    try:
        results = await asyncio.get_event_loop().run_in_executor(None, _sync)
        if not results:
            return f"未找到包含 '{keyword}' 的邮件"
        return f"搜索 '{keyword}' 找到 {len(results)} 封:\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"搜索失败: {e}"


# ==================== 飞书日历技能 ====================

# 缓存日历 ID
_cached_calendar_id = ""


async def _feishu_token():
    cfg = _feishu_config
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                         json={"app_id": cfg["app_id"], "app_secret": cfg["app_secret"]})
        return r.json().get("tenant_access_token", "")


async def _get_primary_calendar_id(token: str) -> str:
    """
    获取主日历 ID
    优先使用 POST /calendars/primary 接口直接获取主日历
    如果失败则回退到遍历日历列表
    """
    global _cached_calendar_id
    if _cached_calendar_id:
        return _cached_calendar_id

    # 方法1: 直接获取主日历 (推荐)
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(
            "https://open.feishu.cn/open-apis/calendar/v4/calendars/primary",
            headers={"Authorization": f"Bearer {token}"},
        )
        data = r.json()

    if data.get("code") == 0:
        calendars = data.get("data", {}).get("calendars", [])
        if calendars:
            cal = calendars[0].get("calendar", {})
            _cached_calendar_id = cal.get("calendar_id", "")
            logger.info(f"获取主日历成功: {cal.get('summary', '未命名')} (ID: {_cached_calendar_id})")
            return _cached_calendar_id

    # 方法2: 回退到遍历日历列表
    logger.warning(f"获取主日历失败 (code={data.get('code')}), 尝试遍历日历列表")
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            "https://open.feishu.cn/open-apis/calendar/v4/calendars",
            headers={"Authorization": f"Bearer {token}"},
            params={"page_size": 50},
        )
        data = r.json()

    if data.get("code") != 0:
        logger.error(f"获取日历列表也失败: {data}")
        return ""

    calendars = data.get("data", {}).get("calendar_list", [])
    for cal in calendars:
        # 优先找主日历 (type=primary) 或 owner 角色
        if cal.get("type") == "primary" or cal.get("role") == "owner":
            _cached_calendar_id = cal.get("calendar_id", "")
            logger.info(f"使用日历: {cal.get('summary', '未命名')} (ID: {_cached_calendar_id})")
            return _cached_calendar_id

    # 如果没找到主日历，用第一个
    if calendars:
        _cached_calendar_id = calendars[0].get("calendar_id", "")
        logger.info(f"使用第一个日历 (ID: {_cached_calendar_id})")
        return _cached_calendar_id

    return ""


@registry.register(
    name="list_calendar_events",
    description="查看飞书日历中未来的日程事件",
    parameters={
        "type": "object",
        "properties": {
            "days_ahead": {"type": "integer", "description": "查看未来几天", "default": 7},
        },
    },
    risk_level="low", category="calendar",
)
async def list_calendar_events(days_ahead: int = 7) -> str:
    cfg = _feishu_config
    if not cfg["app_id"]:
        return "❌ 飞书日历未配置! 请设置 FEISHU_APP_ID / FEISHU_APP_SECRET"

    try:
        token = await _feishu_token()
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=days_ahead)

        # 获取真实的日历 ID
        cal_id = cfg["calendar_id"] if cfg["calendar_id"] and cfg["calendar_id"] != "primary" else ""
        if not cal_id:
            cal_id = await _get_primary_calendar_id(token)
        if not cal_id:
            return "❌ 无法获取日历 ID，请检查日历权限配置"

        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"https://open.feishu.cn/open-apis/calendar/v4/calendars/{cal_id}/events",
                headers={"Authorization": f"Bearer {token}"},
                params={"start_time": str(int(now.timestamp())), "end_time": str(int(end.timestamp())), "page_size": 50},
            )
            data = r.json()

        if data.get("code") != 0:
            return f"获取日历失败: {data.get('msg', '未知错误')} (code: {data.get('code')})"

        events = data.get("data", {}).get("items", [])
        if not events:
            return f"未来 {days_ahead} 天没有日程"

        results = []
        for ev in events:
            eid = ev.get("event_id", "")
            s = ev.get("summary", "(无标题)")
            si = ev.get("start_time", {})
            ei = ev.get("end_time", {})
            if "timestamp" in si:
                st = datetime.fromtimestamp(int(si["timestamp"])).strftime("%m-%d %H:%M")
                et = datetime.fromtimestamp(int(ei.get("timestamp", 0))).strftime("%H:%M")
            else:
                st, et = si.get("date", "?"), ei.get("date", "")
            loc = ev.get("location", {}).get("name", "")
            line = f"📅 {s}\n   {st} ~ {et}"
            if loc:
                line += f" 📍{loc}"
            if eid:
                line += f"\n   ID: {eid[:20]}..."
            results.append(line)

        return f"未来 {days_ahead} 天 ({len(results)} 项):\n\n" + "\n\n".join(results)
    except Exception as e:
        return f"获取日历失败: {e}"


@registry.register(
    name="create_calendar_event",
    description="在飞书日历中创建新事件/提醒",
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "事件标题"},
            "start_time": {"type": "string", "description": "开始时间, 格式 2026-03-10 14:00"},
            "end_time": {"type": "string", "description": "结束时间, 格式 2026-03-10 15:00 (可选, 默认=开始时间+1小时)"},
            "description": {"type": "string", "description": "描述", "default": ""},
            "location": {"type": "string", "description": "地点", "default": ""},
            "reminder_minutes": {"type": "integer", "description": "提前多少分钟提醒 (0=不提醒, 5/15/30/60 等)", "default": 15},
        },
        "required": ["title", "start_time"],
    },
    risk_level="medium", category="calendar",
)
async def create_calendar_event(title: str, start_time: str, end_time: str = "",
                                description: str = "", location: str = "",
                                reminder_minutes: int = 15) -> str:
    cfg = _feishu_config
    if not cfg["app_id"]:
        return "❌ 飞书日历未配置!"

    try:
        token = await _feishu_token()
        s_dt = datetime.strptime(start_time, "%Y-%m-%d %H:%M")
        # 如果没指定结束时间，默认 +1 小时
        if end_time:
            e_dt = datetime.strptime(end_time, "%Y-%m-%d %H:%M")
        else:
            e_dt = s_dt + timedelta(hours=1)

        # 获取真实的日历 ID
        cal_id = cfg["calendar_id"] if cfg["calendar_id"] and cfg["calendar_id"] != "primary" else ""
        if not cal_id:
            cal_id = await _get_primary_calendar_id(token)
        if not cal_id:
            return "❌ 无法获取日历 ID，请检查日历权限配置"

        body = {
            "summary": title,
            "description": description,
            "start_time": {
                "timestamp": str(int(s_dt.timestamp())),
                "timezone": "Asia/Shanghai",
            },
            "end_time": {
                "timestamp": str(int(e_dt.timestamp())),
                "timezone": "Asia/Shanghai",
            },
        }
        if location:
            body["location"] = {"name": location}

        # 设置提醒
        if reminder_minutes > 0:
            body["reminders"] = [{"minutes": reminder_minutes}]

        logger.debug(f"创建日程请求体: {body}")
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"https://open.feishu.cn/open-apis/calendar/v4/calendars/{cal_id}/events",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=body,
            )
            data = r.json()

        if data.get("code") != 0:
            return f"创建失败: {data.get('msg', '未知错误')} (code: {data.get('code')})"

        reminder_info = f"⏰ 提前 {reminder_minutes} 分钟提醒" if reminder_minutes > 0 else "无提醒"
        return f"✅ 日历事件已创建!\n  标题: {title}\n  时间: {start_time} ~ {e_dt.strftime('%Y-%m-%d %H:%M')}\n  {reminder_info}"
    except ValueError:
        return "时间格式错误, 请用: 2026-03-10 14:00"
    except Exception as e:
        return f"创建失败: {e}"


@registry.register(
    name="create_reminder",
    description="快速创建一个提醒 (简化版日历事件)",
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "提醒内容"},
            "remind_at": {"type": "string", "description": "提醒时间, 格式 2026-03-10 14:00"},
            "advance_minutes": {"type": "integer", "description": "提前多少分钟提醒", "default": 0},
        },
        "required": ["title", "remind_at"],
    },
    risk_level="low", category="calendar",
)
async def create_reminder(title: str, remind_at: str, advance_minutes: int = 0) -> str:
    """快速创建提醒 - 本质是一个 15 分钟的日历事件"""
    cfg = _feishu_config
    if not cfg["app_id"]:
        return "❌ 飞书日历未配置!"

    try:
        token = await _feishu_token()
        dt = datetime.strptime(remind_at, "%Y-%m-%d %H:%M")

        # 获取真实的日历 ID
        cal_id = cfg["calendar_id"] if cfg["calendar_id"] and cfg["calendar_id"] != "primary" else ""
        if not cal_id:
            cal_id = await _get_primary_calendar_id(token)
        if not cal_id:
            return "❌ 无法获取日历 ID，请检查日历权限配置"

        body = {
            "summary": f"⏰ {title}",
            "description": "由灵雀助手创建的提醒",
            "start_time": {
                "timestamp": str(int(dt.timestamp())),
                "timezone": "Asia/Shanghai",
            },
            "end_time": {
                "timestamp": str(int((dt + timedelta(minutes=15)).timestamp())),
                "timezone": "Asia/Shanghai",
            },
            "reminders": [{"minutes": advance_minutes}] if advance_minutes >= 0 else [],
        }

        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"https://open.feishu.cn/open-apis/calendar/v4/calendars/{cal_id}/events",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=body,
            )
            data = r.json()

        if data.get("code") != 0:
            return f"创建失败: {data.get('msg', '未知错误')} (code: {data.get('code')})"

        if advance_minutes > 0:
            return f"✅ 提醒已设置!\n  内容: {title}\n  时间: {remind_at}\n  ⏰ 将提前 {advance_minutes} 分钟通知你"
        else:
            return f"✅ 提醒已设置!\n  内容: {title}\n  时间: {remind_at}\n  ⏰ 届时会通知你"
    except ValueError:
        return "时间格式错误, 请用: 2026-03-10 14:00"
    except Exception as e:
        return f"创建失败: {e}"


@registry.register(
    name="delete_calendar_event",
    description="删除飞书日历中的日程事件",
    parameters={
        "type": "object",
        "properties": {
            "event_id": {"type": "string", "description": "日程 event_id（从 list_calendar_events 获取）"},
        },
        "required": ["event_id"],
    },
    risk_level="medium", category="calendar",
)
async def delete_calendar_event(event_id: str) -> str:
    """删除指定的日历事件"""
    cfg = _feishu_config
    if not cfg["app_id"]:
        return "❌ 飞书日历未配置!"

    try:
        token = await _feishu_token()
        cal_id = cfg["calendar_id"] if cfg["calendar_id"] and cfg["calendar_id"] != "primary" else ""
        if not cal_id:
            cal_id = await _get_primary_calendar_id(token)
        if not cal_id:
            return "❌ 无法获取日历 ID"

        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.delete(
                f"https://open.feishu.cn/open-apis/calendar/v4/calendars/{cal_id}/events/{event_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            data = r.json()

        if data.get("code") == 0:
            return f"✅ 日程已删除 (event_id: {event_id[:20]}...)"
        else:
            return f"❌ 删除失败: {data.get('msg', '未知错误')} (code: {data.get('code')})"
    except Exception as e:
        return f"❌ 删除失败: {e}"


@registry.register(
    name="query_freebusy",
    description="查询用户在某个时间段的忙闲状态（用于判断是否有空开会等）",
    parameters={
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "要查询的用户 open_id（不填则查当前应用日历）", "default": ""},
            "start_time": {"type": "string", "description": "开始时间, 格式 2026-03-10 09:00"},
            "end_time": {"type": "string", "description": "结束时间, 格式 2026-03-10 18:00"},
        },
        "required": ["start_time", "end_time"],
    },
    risk_level="low", category="calendar",
)
async def query_freebusy(start_time: str, end_time: str, user_id: str = "") -> str:
    """查询忙闲信息"""
    cfg = _feishu_config
    if not cfg["app_id"]:
        return "❌ 飞书日历未配置!"

    try:
        token = await _feishu_token()
        s_dt = datetime.strptime(start_time, "%Y-%m-%d %H:%M")
        e_dt = datetime.strptime(end_time, "%Y-%m-%d %H:%M")

        body = {
            "time_min": str(int(s_dt.timestamp())),
            "time_max": str(int(e_dt.timestamp())),
        }

        if user_id:
            body["user_id"] = user_id

        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                "https://open.feishu.cn/open-apis/calendar/v4/freebusy/list",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=body,
            )
            data = r.json()

        if data.get("code") != 0:
            return f"❌ 查询失败: {data.get('msg', '未知错误')} (code: {data.get('code')})"

        freebusy_list = data.get("data", {}).get("freebusy_list", [])
        if not freebusy_list:
            return f"✅ {start_time} ~ {end_time} 期间没有日程，是空闲的"

        results = [f"📊 {start_time} ~ {end_time} 忙闲情况:\n"]
        for item in freebusy_list:
            s_ts = item.get("start_time", "")
            e_ts = item.get("end_time", "")
            if s_ts and e_ts:
                s = datetime.fromtimestamp(int(s_ts)).strftime("%H:%M")
                e = datetime.fromtimestamp(int(e_ts)).strftime("%H:%M")
                results.append(f"🔴 {s} ~ {e} 忙碌")

        return "\n".join(results)
    except ValueError:
        return "❌ 时间格式错误, 请用: 2026-03-10 09:00"
    except Exception as e:
        return f"❌ 查询失败: {e}"


# ==================== 飞书消息提醒（推荐） ====================

# 当前 chat_id（使用 contextvars 实现异步并发安全）
import contextvars
_current_chat_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("email_chat_id", default="")


def set_current_chat_id(chat_id: str):
    """设置当前会话 ID（由 FeishuChannel 调用，基于 contextvars 并发安全）"""
    _current_chat_id_var.set(chat_id)


@registry.register(
    name="set_feishu_reminder",
    description="设置提醒（同时加入日历 + 到时间通过飞书消息通知，推荐使用）",
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "提醒内容"},
            "remind_at": {"type": "string", "description": "提醒时间, 格式 2026-03-10 14:00"},
            "add_to_calendar": {"type": "boolean", "description": "是否同时加入日历", "default": True},
        },
        "required": ["title", "remind_at"],
    },
    risk_level="low", category="reminder",
)
async def set_feishu_reminder(title: str, remind_at: str, add_to_calendar: bool = True) -> str:
    """设置提醒 - 同时加入日历并到时间通过飞书消息推送"""
    current_chat_id = _current_chat_id_var.get()
    cfg = _feishu_config

    if not current_chat_id:
        return "❌ 无法获取会话 ID，请通过飞书发送消息"

    try:
        dt = datetime.strptime(remind_at, "%Y-%m-%d %H:%M")
        now = datetime.now()

        if dt <= now:
            return f"❌ 提醒时间 {remind_at} 已过去，请设置未来的时间"

        results = []

        # 1. 保存到本地（用于飞书消息提醒）
        reminders = _load_reminders()
        reminders.append({
            "title": title,
            "remind_at": remind_at,
            "chat_id": current_chat_id,
            "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        })
        _save_reminders(reminders)
        results.append("✅ 飞书消息提醒已设置")

        # 2. 同时加入日历（如果配置了飞书）
        calendar_ok = False
        if add_to_calendar and cfg["app_id"]:
            try:
                token = await _feishu_token()
                cal_id = cfg["calendar_id"] if cfg["calendar_id"] and cfg["calendar_id"] != "primary" else ""
                if not cal_id:
                    cal_id = await _get_primary_calendar_id(token)

                if cal_id:
                    body = {
                        "summary": f"⏰ {title}",
                        "description": "由灵雀助手创建的提醒",
                        "start_time": {
                            "timestamp": str(int(dt.timestamp())),
                            "timezone": "Asia/Shanghai",
                        },
                        "end_time": {
                            "timestamp": str(int((dt + timedelta(minutes=15)).timestamp())),
                            "timezone": "Asia/Shanghai",
                        },
                        "reminders": [{"minutes": 0}],
                    }
                    async with httpx.AsyncClient(timeout=10) as c:
                        r = await c.post(
                            f"https://open.feishu.cn/open-apis/calendar/v4/calendars/{cal_id}/events",
                            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                            json=body,
                        )
                        data = r.json()
                    if data.get("code") == 0:
                        calendar_ok = True
                        results.append("✅ 已加入日历")
                    else:
                        results.append(f"⚠️ 日历添加失败: {data.get('msg', '未知')}")
            except Exception as e:
                results.append(f"⚠️ 日历添加失败: {e}")

        # 计算距离提醒的时间
        delta = dt - now
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes = remainder // 60

        if hours > 0:
            time_str = f"{hours}小时{minutes}分钟"
        else:
            time_str = f"{minutes}分钟"

        return f"{'  '.join(results)}\n\n📌 内容: {title}\n⏰ 时间: {remind_at}\n🔔 {time_str}后会通过飞书消息通知你" + ("\n📅 可在日历中查看" if calendar_ok else "")

    except ValueError:
        return "❌ 时间格式错误, 请用: 2026-03-10 14:00"
    except Exception as e:
        return f"❌ 设置失败: {e}"


@registry.register(
    name="list_reminders",
    description="查看已设置的飞书消息提醒列表",
    parameters={"type": "object", "properties": {}},
    risk_level="low", category="reminder",
)
async def list_reminders() -> str:
    """列出所有待发送的提醒"""
    reminders = _load_reminders()

    if not reminders:
        return "📭 没有待发送的提醒"

    now = datetime.now()
    lines = [f"📋 待发送的提醒 ({len(reminders)} 个):\n"]

    for i, r in enumerate(reminders, 1):
        dt = datetime.strptime(r["remind_at"], "%Y-%m-%d %H:%M")
        delta = dt - now
        if delta.total_seconds() > 0:
            hours, remainder = divmod(int(delta.total_seconds()), 3600)
            minutes = remainder // 60
            if hours > 0:
                time_str = f"还有 {hours}小时{minutes}分钟"
            else:
                time_str = f"还有 {minutes}分钟"
        else:
            time_str = "已过期"

        lines.append(f"{i}. {r['title']}\n   ⏰ {r['remind_at']} ({time_str})")

    return "\n".join(lines)


@registry.register(
    name="cancel_reminder",
    description="取消某个飞书消息提醒",
    parameters={
        "type": "object",
        "properties": {
            "index": {"type": "integer", "description": "提醒序号 (从 list_reminders 获取)"},
        },
        "required": ["index"],
    },
    risk_level="low", category="reminder",
)
async def cancel_reminder(index: int) -> str:
    """取消指定序号的提醒"""
    reminders = _load_reminders()

    if not reminders:
        return "❌ 没有待发送的提醒"

    if index < 1 or index > len(reminders):
        return f"❌ 序号无效，请输入 1-{len(reminders)} 之间的数字"

    removed = reminders.pop(index - 1)
    _save_reminders(reminders)

    return f"✅ 已取消提醒: {removed['title']} ({removed['remind_at']})"
