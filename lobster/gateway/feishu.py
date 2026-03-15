"""
🐦 灵雀 LingQue - 飞书通道适配器 (P2 版)

P1: 卡片消息 / 按钮确认 / 断线重连 / 消息去重 / Markdown渲染 / 消息提醒
P2: 外部 Webhook 接入 (GitHub/Sentry 等)
"""

import json
import asyncio
import contextvars
import logging
import re
import uuid
import time
import threading
from aiohttp import web
import httpx

from .base import BaseChannel
from ..agent.core import Agent
from ..skills.email_calendar import set_reminder_callback, set_current_chat_id, check_and_send_reminders
from ..scheduler.webhook_handler import WebhookHandler

logger = logging.getLogger("lingque.feishu")

# 异步安全的会话 ID 上下文变量，每个并发请求链拥有独立值
_active_chat_id: contextvars.ContextVar[str] = contextvars.ContextVar("active_chat_id", default="")
FEISHU_API = "https://open.feishu.cn/open-apis"


class FeishuChannel(BaseChannel):

    def __init__(self, agent: Agent, config):
        super().__init__(agent)
        self.app_id = config.feishu.app_id
        self.app_secret = config.feishu.app_secret
        self.verification_token = config.feishu.verification_token
        self.encrypt_key = config.feishu.encrypt_key
        self.config = config

        self.bot_open_id = config.feishu.bot_open_id
        self._tenant_access_token = ""
        self._token_expire_time = 0

        # per-chat UI 状态（thinking_card_id / progress_message_id / last_msg_id）
        self._chat_states: dict[str, dict] = {}

        # 高风险确认: action_id → Future
        self._pending_confirmations: dict[str, asyncio.Future] = {}
        # 消息去重
        self._processed_msg_ids: dict[str, float] = {}
        self._msg_id_ttl = 300
        # 并发限制
        self._semaphore = asyncio.Semaphore(3)
        # 群成员缓存
        self._member_cache: dict[str, tuple[float, list[dict]]] = {}

        self.agent.set_confirm_callback(self._ask_confirmation_via_card)
        self.agent.set_progress_callback(self._show_progress)
        self.agent.set_plan_callback(self._show_plan)

        # 设置提醒回调
        set_reminder_callback(self._send_reminder_message)

        # P2: 外部 Webhook 处理器
        self._webhook_handler = WebhookHandler(
            agent=agent,
            notify_callback=self._send_webhook_notification,
        )
        if config.webhook.github_secret:
            self._webhook_handler.set_secret("github", config.webhook.github_secret)
        if config.webhook.sentry_secret:
            self._webhook_handler.set_secret("sentry", config.webhook.sentry_secret)

    def _get_chat_state(self, chat_id: str = "") -> dict:
        """获取 per-chat UI 状态，自动创建"""
        cid = chat_id or _active_chat_id.get()
        if not cid:
            return {"thinking_card_id": None, "progress_message_id": None, "last_user_message_id": ""}
        if cid not in self._chat_states:
            self._chat_states[cid] = {
                "thinking_card_id": None,
                "progress_message_id": None,
                "last_user_message_id": "",
            }
        return self._chat_states[cid]

    # ==================== 启动 & 重连 ====================

    async def start(self, host: str = "0.0.0.0", port: int = 9000):
        mode = getattr(self.config.feishu, "connection_mode", "webhook")
        if mode == "websocket":
            await self._start_websocket(host, port)
        else:
            await self._start_webhook(host, port)

    async def _start_webhook(self, host: str = "0.0.0.0", port: int = 9000):
        """Webhook 模式：启动 aiohttp 全量 HTTP 服务"""
        await self._ensure_token()

        app = web.Application()
        app.router.add_post("/webhook/feishu", self._handle_webhook)
        app.router.add_post("/webhook/feishu/card", self._handle_card_action)
        app.router.add_post("/webhook/external", self._handle_external_webhook)
        app.router.add_post("/webhook/github", self._handle_github_webhook)
        app.router.add_post("/webhook/sentry", self._handle_sentry_webhook)
        app.router.add_get("/health", self._health_check)

        runner = web.AppRunner(app)
        await runner.setup()

        retry_delay = 1
        while True:
            try:
                site = web.TCPSite(runner, host, port)
                await site.start()
                logger.info(f"🐦 飞书 Webhook: http://{host}:{port}/webhook/feishu")
                logger.info(f"📮 卡片回调: http://{host}:{port}/webhook/feishu/card")
                logger.info(f"🔗 外部 Webhook: http://{host}:{port}/webhook/external")
                logger.info(f"🐙 GitHub Webhook: http://{host}:{port}/webhook/github")
                logger.info(f"🚨 Sentry Webhook: http://{host}:{port}/webhook/sentry")
                break
            except OSError as e:
                logger.warning(f"端口 {port} 绑定失败: {e}, {retry_delay}s 后重试")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)

        asyncio.create_task(self._background_tasks())

        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await runner.cleanup()

    async def _start_websocket(self, host: str = "0.0.0.0", port: int = 9000):
        """WebSocket 长连接模式：使用 lark-oapi SDK 接收事件，可选精简 HTTP 服务处理外部 Webhook"""
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            P2CardActionTrigger, P2CardActionTriggerResponse,
        )

        self._loop = asyncio.get_running_loop()
        await self._ensure_token()

        # 外部 Webhook 仍需 HTTP 服务
        runner = None
        has_external_webhooks = (
            self.config.webhook.github_secret or self.config.webhook.sentry_secret
        )
        if has_external_webhooks:
            app = web.Application()
            app.router.add_post("/webhook/external", self._handle_external_webhook)
            app.router.add_post("/webhook/github", self._handle_github_webhook)
            app.router.add_post("/webhook/sentry", self._handle_sentry_webhook)
            app.router.add_get("/health", self._health_check)
            runner = web.AppRunner(app)
            await runner.setup()
            try:
                site = web.TCPSite(runner, host, port)
                await site.start()
                logger.info(f"🔗 外部 Webhook HTTP 服务: http://{host}:{port}")
            except OSError as e:
                logger.warning(f"外部 Webhook 端口 {port} 绑定失败: {e}，外部 Webhook 不可用")
                runner = None

        channel = self

        def on_message_receive(data: P2ImMessageReceiveV1) -> None:
            """lark-oapi 消息事件回调（同步，子线程执行）"""
            try:
                event = data.event
                msg = event.message
                sender = event.sender

                mentions_raw = msg.mentions or []
                mentions = []
                for m in mentions_raw:
                    mentions.append({
                        "key": getattr(m, "key", ""),
                        "id": {"open_id": getattr(getattr(m, "id", None), "open_id", "") if hasattr(m, "id") else ""},
                        "name": getattr(m, "name", ""),
                    })

                body = {
                    "event": {
                        "sender": {
                            "sender_id": {
                                "open_id": sender.sender_id.open_id if sender and sender.sender_id else "",
                            },
                        },
                        "message": {
                            "message_id": msg.message_id or "",
                            "chat_id": msg.chat_id or "",
                            "chat_type": msg.chat_type or "p2p",
                            "message_type": msg.message_type or "",
                            "content": msg.content or "{}",
                            "mentions": mentions,
                        },
                    },
                }
                asyncio.run_coroutine_threadsafe(
                    channel._handle_message_event(body),
                    channel._loop,
                )
            except Exception as e:
                logger.error(f"[WS] 消息事件处理异常: {e}", exc_info=True)

        def on_card_action(data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
            """lark-oapi 卡片回调（同步，子线程执行）- 必须返回响应以更新卡片"""
            try:
                event = data.event
                action = event.action if hasattr(event, "action") else None
                if not action:
                    return P2CardActionTriggerResponse({"toast": {"type": "info", "content": "已收到"}})

                action_value = getattr(action, "value", {}) or {}
                if isinstance(action_value, str):
                    try:
                        action_value = json.loads(action_value)
                    except (json.JSONDecodeError, TypeError):
                        action_value = {}

                action_id = action_value.get("action_id", "")
                confirmed_raw = action_value.get("confirmed", False)
                confirmed = confirmed_raw in (True, "true", "True", "1")

                future = channel._pending_confirmations.pop(action_id, None)
                if future and not future.done():
                    channel._loop.call_soon_threadsafe(future.set_result, confirmed)
                    logger.info(f"[WS] 卡片确认: action_id={action_id}, confirmed={confirmed}")

                if confirmed:
                    status_text = "✅ **操作已确认执行**"
                    template_color = "green"
                    title = "✅ 已确认"
                    toast_type = "success"
                    toast_content = "已确认执行"
                else:
                    status_text = "❌ **操作已取消**"
                    template_color = "grey"
                    title = "❌ 已取消"
                    toast_type = "warning"
                    toast_content = "已取消"

                updated_card = {
                    "config": {"update_multi": True},
                    "header": {
                        "template": template_color,
                        "title": {"tag": "plain_text", "content": title},
                    },
                    "elements": [
                        {
                            "tag": "div",
                            "text": {"tag": "lark_md", "content": status_text},
                        },
                    ],
                }

                return P2CardActionTriggerResponse({
                    "toast": {"type": toast_type, "content": toast_content},
                    "card": {"type": "raw", "data": updated_card},
                })

            except Exception as e:
                logger.error(f"[WS] 卡片回调处理异常: {e}", exc_info=True)
                return P2CardActionTriggerResponse({"toast": {"type": "error", "content": "处理异常"}})

        def on_reaction_created(data) -> None:
            """表情回复事件 - 静默忽略（灵雀自己添加的表情会触发此事件）"""
            pass

        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(on_message_receive)
            .register_p2_card_action_trigger(on_card_action)
            .register_p2_im_message_reaction_created_v1(on_reaction_created)
            .build()
        )

        logger.info("🔌 飞书长连接模式启动中...")

        app_id = self.app_id
        app_secret = self.app_secret

        def _run_ws_client():
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            # lark-oapi ws.client 使用模块级 loop 变量，必须替换为子线程的循环
            import lark_oapi.ws.client as _ws_mod
            _ws_mod.loop = new_loop
            try:
                client = lark.ws.Client(
                    app_id,
                    app_secret,
                    event_handler=handler,
                    log_level=lark.LogLevel.INFO,
                )
                client.start()
            except Exception as e:
                logger.error(f"[WS] 长连接线程异常: {e}", exc_info=True)

        ws_thread = threading.Thread(target=_run_ws_client, daemon=True)
        ws_thread.start()

        logger.info("🔌 飞书 WebSocket 长连接已启动 (自动重连)")

        asyncio.create_task(self._background_tasks())

        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            if runner:
                await runner.cleanup()

    async def _background_tasks(self):
        """后台任务：Token 刷新、消息去重清理、提醒检查"""
        reminder_check_interval = 30  # 每 30 秒检查一次提醒
        token_refresh_interval = 600  # 每 10 分钟刷新 token
        last_token_refresh = 0

        while True:
            await asyncio.sleep(reminder_check_interval)

            # 检查并发送到期的提醒
            try:
                sent = await check_and_send_reminders()
                if sent:
                    logger.info(f"已发送 {sent} 条提醒")
            except Exception as e:
                logger.error(f"检查提醒失败: {e}")

            # 定期刷新 token 和清理消息 ID
            last_token_refresh += reminder_check_interval
            if last_token_refresh >= token_refresh_interval:
                await self._ensure_token()
                self._cleanup_msg_ids()
                last_token_refresh = 0

    async def _send_reminder_message(self, chat_id: str, content: str):
        """发送提醒消息（供提醒系统回调）"""
        await self.send_card(chat_id, "⏰ 灵雀提醒", content)

    async def _send_webhook_notification(self, chat_id: str, content: str):
        """发送 Webhook 事件通知"""
        target_chat = chat_id or _active_chat_id.get()
        if target_chat:
            await self.send_card(target_chat, "🔔 外部事件通知", content)

    # ==================== P2: 外部 Webhook 处理 ====================

    async def _handle_external_webhook(self, request: web.Request) -> web.Response:
        """处理通用外部 Webhook"""
        try:
            body = await request.read()
            headers = dict(request.headers)
            result = await self._webhook_handler.handle(body, headers, source="auto")
            return web.json_response(result)
        except Exception as e:
            logger.error(f"处理外部 Webhook 失败: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_github_webhook(self, request: web.Request) -> web.Response:
        """处理 GitHub Webhook"""
        try:
            body = await request.read()
            headers = dict(request.headers)

            # 更新 webhook handler 的通知 chat_id
            self._webhook_handler.notify_chat_id = _active_chat_id.get()

            result = await self._webhook_handler.handle(body, headers, source="github")
            return web.json_response(result)
        except Exception as e:
            logger.error(f"处理 GitHub Webhook 失败: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_sentry_webhook(self, request: web.Request) -> web.Response:
        """处理 Sentry Webhook"""
        try:
            body = await request.read()
            headers = dict(request.headers)

            self._webhook_handler.notify_chat_id = _active_chat_id.get()

            result = await self._webhook_handler.handle(body, headers, source="sentry")
            return web.json_response(result)
        except Exception as e:
            logger.error(f"处理 Sentry Webhook 失败: {e}")
            return web.json_response({"error": str(e)}, status=500)

    # ==================== Webhook 处理 ====================

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.Response(status=400, text="Invalid JSON")

        # 处理 URL 验证 (challenge)
        if "challenge" in body:
            return web.json_response({"challenge": body["challenge"]})

        # 验证请求 token (事件订阅 v2 格式: header.token)
        header = body.get("header", {})
        if self.verification_token:
            token = header.get("token", "")
            if token != self.verification_token:
                logger.warning("Webhook token 验证失败")
                return web.Response(status=403, text="Invalid token")

        event_type = header.get("event_type", "")

        if event_type == "im.message.receive_v1":
            asyncio.create_task(self._handle_message_event(body))

        return web.Response(status=200, text="ok")

    async def _handle_card_action(self, request: web.Request) -> web.Response:
        """
        处理飞书卡片回传交互回调 (兼容 schema 2.0 和旧版)
        
        关键: 返回格式必须是 {"card": {"type": "raw", "data": <card>}}
              否则飞书会忽略卡片更新，按钮不会消失
        """
        try:
            body = await request.json()
        except Exception:
            return web.Response(status=400)

        # 处理 URL 验证 (challenge)
        if "challenge" in body:
            return web.json_response({"challenge": body["challenge"]})

        try:
            return await self._process_card_action(body)
        except Exception as e:
            logger.error(f"卡片回调处理异常: {e}", exc_info=True)
            return web.Response(status=200, text="ok")

    async def _process_card_action(self, body: dict) -> web.Response:
        """实际处理卡片回调逻辑"""
        # 判断回调版本并解析
        schema = body.get("schema", "")
        if schema == "2.0":
            header = body.get("header", {})
            event = body.get("event", {})
            if not isinstance(event, dict):
                event = {}
            token = header.get("token", "") if isinstance(header, dict) else ""
            action = event.get("action", {})
        else:
            token = body.get("token", "")
            action = body.get("action", {})

        # action 本身可能不是 dict
        if isinstance(action, str):
            try:
                action = json.loads(action)
            except (json.JSONDecodeError, TypeError):
                action = {}
        if not isinstance(action, dict):
            logger.warning(f"卡片回调 action 类型异常: {type(action)}")
            return web.Response(status=200, text="ok")

        # 解析 action.value (飞书规范为 object，但防御性处理 string)
        action_value = action.get("value", {})
        if isinstance(action_value, str):
            try:
                parsed = json.loads(action_value)
                action_value = parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, TypeError):
                action_value = {}
        if not isinstance(action_value, dict):
            action_value = {}

        action_id = action_value.get("action_id", "")
        confirmed_raw = action_value.get("confirmed", False)
        confirmed = confirmed_raw in (True, "true", "True", "1")
        
        # token 校验: 配置了 verification_token 时必须匹配
        if self.verification_token and token != self.verification_token:
            logger.warning(f"卡片回调 token 验证失败，拒绝请求")
            return web.Response(status=200, text="ok")

        # 唤醒等待确认的 Future
        future = self._pending_confirmations.pop(action_id, None)
        if future and not future.done():
            future.set_result(confirmed)
            logger.info(f"卡片确认: action_id={action_id}, confirmed={confirmed}")

        # 构建更新后的卡片（移除按钮，显示状态）
        if confirmed:
            status_text = "✅ **操作已确认执行**"
            template_color = "green"
            title = "✅ 已确认"
        else:
            status_text = "❌ **操作已取消**"
            template_color = "grey"
            title = "❌ 已取消"

        updated_card_content = {
            "config": {"update_multi": True},
            "header": {
                "template": template_color,
                "title": {"tag": "plain_text", "content": title},
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": status_text},
                },
            ],
        }

        return web.json_response({
            "toast": {
                "type": "success" if confirmed else "warning",
                "content": "已确认执行" if confirmed else "已取消",
            },
            "card": {
                "type": "raw",
                "data": updated_card_content,
            },
        })

    async def _handle_message_event(self, body: dict):
        """消息入口: 去重 → 鉴权 → 并发控制 → 处理"""
        try:
            event = body.get("event", {})
            message = event.get("message", {})
            msg_id = message.get("message_id", "")
            chat_id = message.get("chat_id", "")

            # 去重
            if msg_id and msg_id in self._processed_msg_ids:
                return
            if msg_id:
                self._processed_msg_ids[msg_id] = time.time()

            # 用户鉴权
            sender = event.get("sender", {})
            sender_id = sender.get("sender_id", {}).get("open_id", "")
            allowed_users = self.config.feishu.allowed_users
            if allowed_users:
                allowed_list = [u.strip() for u in allowed_users.split(",") if u.strip()]
                if allowed_list and sender_id not in allowed_list:
                    logger.warning(f"未授权用户尝试访问: {sender_id}")
                    return

            # 并发控制
            if self._semaphore.locked():
                if chat_id:
                    await self.send_card(chat_id, "🐦 灵雀", "⏳ 当前有其他任务在处理，请稍等片刻后重新发送...")
                return

            async with self._semaphore:
                await self._process_feishu_message(event, message, msg_id, chat_id, sender_id)

        except Exception as e:
            logger.error(f"处理飞书消息失败: {e}", exc_info=True)

    async def _process_feishu_message(self, event: dict, message: dict,
                                       msg_id: str, chat_id: str, sender_id: str):
        """实际消息处理: 群聊@检测 + 会话隔离 + 发送者识别"""
        msg_type = message.get("message_type", "")
        msg_content = message.get("content", "{}")
        chat_type = message.get("chat_type", "p2p")

        _active_chat_id.set(chat_id)
        self._get_chat_state(chat_id)["last_user_message_id"] = msg_id
        set_current_chat_id(chat_id)

        try:
            content = json.loads(msg_content)
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"消息内容 JSON 解析失败: {msg_content[:200]}")
            return

        text = ""
        images_b64: list[str] = []
        file_paths: list[str] = []

        if msg_type == "text":
            text = content.get("text", "").strip()

        elif msg_type == "image":
            image_key = content.get("image_key", "")
            if image_key:
                b64 = await self._download_image_as_base64(msg_id, image_key)
                if b64:
                    images_b64.append(b64)
                else:
                    await self.send_card(chat_id, "🐦 提示", "图片下载失败，请重试")
                    return
            text = content.get("text", "").strip() or "请分析这张图片"

        elif msg_type == "post":
            parsed = await self._parse_post_message(content, msg_id)
            text = parsed["text"]
            images_b64 = parsed["images"]
            file_paths = parsed["files"]
            if not text and images_b64:
                text = "请分析这张图片"
            if not text and file_paths:
                text = "用户上传了文件，请分析"

        elif msg_type == "file":
            file_key = content.get("file_key", "")
            file_name = content.get("file_name", "unknown")
            if file_key:
                fpath = await self._download_file(msg_id, file_key, file_name)
                if fpath:
                    file_paths.append(fpath)
                else:
                    await self.send_card(chat_id, "🐦 提示", "文件下载失败，请重试")
                    return
            text = "用户上传了文件，请分析"

        else:
            await self.send_card(chat_id, "🐦 提示", "暂不支持该消息类型")
            return

        if not text and not images_b64 and not file_paths:
            return

        # 群聊: 只响应 @ 机器人的消息（所有消息类型统一用 message.mentions 检测）
        if chat_type == "group":
            mentions = message.get("mentions", [])
            if self.bot_open_id:
                bot_mentioned = any(
                    m.get("id", {}).get("open_id") == self.bot_open_id
                    for m in mentions
                )
                if not bot_mentioned:
                    return
            # text 类型的 @占位符处理；post 类型 at 标签独立于文本，无需处理
            if msg_type == "text":
                for m in mentions:
                    mention_key = m.get("key", "")
                    if not mention_key:
                        continue
                    mention_open_id = m.get("id", {}).get("open_id", "")
                    if mention_open_id == self.bot_open_id:
                        text = text.replace(mention_key, "").strip()
                    else:
                        mention_name = m.get("name", "某人")
                        text = text.replace(mention_key, f"@{mention_name}")

        # 文件路径追加到消息文本
        if file_paths:
            file_info = "\n".join(f"[已接收文件: {fp}]" for fp in file_paths)
            text = f"{text}\n{file_info}" if text else file_info

        if not text and not images_b64:
            return

        # 生成 session_id
        if chat_type == "group":
            session_id = f"group:{chat_id}"
        else:
            session_id = f"dm:{sender_id}" if sender_id else "default"

        # 命令处理
        if text == "/status":
            await self.send_card(chat_id, "🐦 灵雀状态", f"```\n{self.agent.get_status(session_id)}\n```")
            return
        if text == "/clear":
            self.agent.memory.set_session(session_id)
            self.agent.memory.clear_session()
            await self.send_card(chat_id, "🐦 已清空", "会话记忆已清除")
            return
        if text.startswith("/model"):
            await self._handle_model_command(text, session_id, chat_id)
            return
        if text == "/reload":
            await self._handle_reload_command(chat_id)
            return

        # 群聊消息加发送者标识（让 Agent 区分不同用户）
        if chat_type == "group" and sender_id:
            text = f"[来自用户 {sender_id[-8:]}] {text}"

        self.reset_progress()

        if msg_id:
            asyncio.create_task(self.add_reaction(msg_id, "OnIt"))

        await self._send_thinking_card(chat_id, text)

        logger.info(f"收到飞书消息 [{session_id}]: {text[:100]}"
                    + (f" +{len(images_b64)}张图片" if images_b64 else "")
                    + (f" +{len(file_paths)}个文件" if file_paths else ""))
        response = await self.agent.process_message(
            text, session_id=session_id, images=images_b64, user_id=sender_id
        )

        self.reset_progress()

        if msg_id:
            asyncio.create_task(self.add_reaction(msg_id, "DONE"))

        # 群聊回复时 @ 提问者
        if chat_type == "group" and sender_id:
            await self.send_card(chat_id, "🐦 灵雀", response, mention_user_id=sender_id)
        else:
            await self.send_card(chat_id, "🐦 灵雀", response)

    async def _send_thinking_card(self, chat_id: str, user_message: str):
        """发送友好的处理中提示"""
        msg = user_message.lower()
        
        if any(kw in msg for kw in ["截图", "screenshot", "打开", "访问"]):
            hint = "🌐 正在打开浏览器..."
        elif any(kw in msg for kw in ["邮件", "email", "发送"]):
            hint = "📧 正在处理邮件..."
        elif any(kw in msg for kw in ["文件", "读取", "写入", "保存"]):
            hint = "📁 正在处理文件..."
        elif any(kw in msg for kw in ["日历", "日程", "提醒"]):
            hint = "📅 正在查看日历..."
        elif any(kw in msg for kw in ["代码", "运行", "执行", "python"]):
            hint = "💻 正在准备执行..."
        elif any(kw in msg for kw in ["搜索", "查找", "找"]):
            hint = "🔍 正在搜索..."
        else:
            hint = "🤔 正在思考..."
        
        card = {
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": "🐦 灵雀"},
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": hint},
                },
            ],
        }
        state = self._get_chat_state(chat_id)
        state["thinking_card_id"] = await self._send_interactive_card_with_id(chat_id, card)

    @staticmethod
    def _extract_plan_steps(text: str) -> str:
        """从 LLM 计划输出中只提取编号步骤，找不到则返回空"""
        import re
        steps: list[str] = []
        for line in text.split("\n"):
            stripped = line.strip()
            if re.match(r"^\d+[\.\、\)）]\s*\S", stripped):
                clean_line = re.sub(r"[`<>|]", "", stripped)
                if len(clean_line) > 4:
                    steps.append(clean_line)
        return "\n".join(steps)

    async def _show_plan(self, plan_text: str):
        """将规划步骤显示在思考卡片上"""
        state = self._get_chat_state()
        thinking_id = state.get("thinking_card_id")
        if not thinking_id or not plan_text:
            return
        clean = self._extract_plan_steps(plan_text)
        if not clean:
            return
        card = {
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": "🐦 灵雀 · 执行计划"},
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": clean},
                },
            ],
        }
        try:
            await self._update_card(thinking_id, card)
        except Exception as e:
            logger.warning(f"更新规划卡片失败: {e}")

    async def _handle_reload_command(self, chat_id: str):
        """热重载 .env 配置，无需重启服务"""
        import os
        from dotenv import load_dotenv
        from ..config import Config, LLMConfig
        from ..skills.file_ops import set_allowed_paths

        reloaded = []
        errors = []

        try:
            load_dotenv(override=True)

            new_config = Config()

            # 1. LLM 配置（API Key / 模型 / 自定义 Provider）
            old_provider = self.agent.llm.primary
            old_providers_count = len(self.agent.llm.providers)
            self.agent.llm.config = new_config
            self.agent.llm.providers.clear()
            self.agent.llm._custom_providers.clear()
            self.agent.llm.primary = new_config.llm.provider
            self.agent.llm._init_providers()
            new_providers = list(self.agent.llm.providers.keys())
            reloaded.append(f"LLM: 主模型 **{new_config.llm.provider}**, "
                          f"可用 {len(new_providers)} 个 ({', '.join(new_providers)})")

            # 2. Agent 超时参数
            agent_cfg = new_config.agent
            self.agent.llm._llm_timeout = agent_cfg.llm_timeout
            self.agent._task_timeout = agent_cfg.task_timeout
            self.agent._tool_timeout = agent_cfg.tool_timeout
            self.agent.max_loops = agent_cfg.max_loops
            reloaded.append(f"Agent: 任务超时={agent_cfg.task_timeout}s, "
                          f"LLM超时={agent_cfg.llm_timeout}s, 最大循环={agent_cfg.max_loops}")

            # 3. 安全配置
            allowed = new_config.security.get_allowed_paths()
            if allowed:
                set_allowed_paths(allowed)
            else:
                set_allowed_paths([str(new_config.workspace_dir.resolve())])
            self.agent.require_confirmation = new_config.security.require_confirmation
            reloaded.append(f"安全: 确认={new_config.security.require_confirmation}, "
                          f"路径={len(allowed or [1])}个")

            # 4. 飞书白名单
            old_users = self.config.feishu.allowed_users
            self.config = new_config
            reloaded.append(f"飞书白名单: {'已更新' if new_config.feishu.allowed_users != old_users else '未变'}")

            # 5. 日志级别
            import logging
            log_level = getattr(logging, new_config.log_level.upper(), logging.INFO)
            logging.getLogger().setLevel(log_level)
            reloaded.append(f"日志级别: {new_config.log_level}")

        except Exception as e:
            errors.append(f"重载异常: {e}")
            logger.error(f"配置热重载失败: {e}", exc_info=True)

        # 构建反馈消息
        lines = ["**✅ 配置已热重载**\n"]
        for item in reloaded:
            lines.append(f"- {item}")

        if errors:
            lines.append("\n**⚠️ 部分重载失败:**")
            for err in errors:
                lines.append(f"- {err}")

        lines.append("\n---")
        lines.append("*以下配置修改需要重启才能生效:*")
        lines.append("飞书 App ID/Secret、连接模式、通道类型、端口")

        await self.send_card(chat_id, "🔄 配置热重载", "\n".join(lines))
        logger.info(f"配置热重载完成: {len(reloaded)} 项成功, {len(errors)} 项失败")

    async def _handle_model_command(self, text: str, session_id: str, chat_id: str):
        """处理 /model 命令：查看、切换模型"""
        parts = text.strip().split()

        if len(parts) == 1:
            models = self.agent.llm.list_models()
            active = self.agent.llm.get_active_provider(session_id)
            lines = ["**当前可用模型:**\n"]
            for m in models:
                if not m["available"]:
                    continue
                marker = " ✅" if m["name"] == active else ""
                lines.append(f"- **{m['name']}**  {m['description']}  `{m['model_id']}`{marker}")
            lines.append(f"\n切换命令: `/model <名称>`\n例如: `/model doubao`")
            await self.send_card(chat_id, "🤖 模型管理", "\n".join(lines))
            return

        target = parts[1].lower().strip()

        alias_map = {
            "deepseek": "deepseek",
            "ds": "deepseek",
            "doubao": "doubao",
            "db": "doubao",
            "豆包": "doubao",
            "claude": "anthropic",
            "anthropic": "anthropic",
            "openai": "openai",
            "gpt": "openai",
        }

        provider_name = alias_map.get(target, target)

        if self.agent.llm.set_session_model(session_id, provider_name):
            model_id = self.agent.llm.providers[provider_name].model
            display = self.agent.llm.MODEL_DISPLAY_NAMES.get(provider_name, provider_name)
            await self.send_card(
                chat_id, "🤖 模型已切换",
                f"已切换到 **{provider_name}** ({display})\n模型: `{model_id}`"
            )
        else:
            available = [m["name"] for m in self.agent.llm.list_models() if m["available"]]
            await self.send_card(
                chat_id, "🤖 切换失败",
                f"模型 `{target}` 不可用。\n可用模型: {', '.join(available)}"
            )

    # ==================== 卡片消息 (Markdown) ====================

    @staticmethod
    def _to_feishu_md(text: str) -> str:
        """将标准 Markdown 转换为飞书卡片 markdown 兼容格式（不处理表格）

        表格由 _content_to_elements 单独处理为 v2 table 组件。
        """
        lines = text.split('\n')
        result = []
        for line in lines:
            header_match = re.match(r'^(#{1,6})\s+(.+)$', line)
            if header_match:
                result.append(f'**{header_match.group(2).strip()}**')
                continue
            if re.match(r'^-{3,}$', line.strip()):
                result.append('')
                continue
            result.append(line)
        return '\n'.join(result)

    @staticmethod
    def _parse_md_table(lines: list[str], start: int) -> tuple[list[str], list[str], int]:
        """解析 markdown 表格，返回 (headers, table_lines, end_index)"""
        headers = [c.strip() for c in lines[start].strip().strip('|').split('|')]
        headers = [h for h in headers if h]
        table_lines = [lines[start], lines[start + 1]]
        j = start + 2
        while j < len(lines) and '|' in lines[j] and lines[j].strip():
            table_lines.append(lines[j])
            j += 1
        return headers, table_lines, j

    @staticmethod
    def _md_table_to_v2_element(headers: list[str], table_lines: list[str]) -> dict:
        """将 markdown 表格转为飞书 v2 card table 组件

        根据官方文档: columns 用 name(key)+display_name, rows 用对象格式。
        """
        columns = []
        for i, h in enumerate(headers):
            columns.append({
                "name": f"c{i}",
                "display_name": h,
                "data_type": "text",
                "width": "auto",
            })

        rows = []
        for row_line in table_lines[2:]:
            if re.match(r'^[\s|:\-]+$', row_line):
                continue
            cells = [c.strip() for c in row_line.strip().strip('|').split('|')]
            cells = [c for c in cells if c or len(cells) <= len(headers) + 2]
            row = {}
            for i in range(len(headers)):
                row[f"c{i}"] = cells[i] if i < len(cells) else ""
            rows.append(row)

        return {
            "tag": "table",
            "page_size": max(len(rows), 1),
            "columns": columns,
            "rows": rows,
        }

    @staticmethod
    def _md_table_to_bullets(headers: list[str], table_lines: list[str]) -> str:
        """将 markdown 表格转为 bullet 列表（v1 降级方案）"""
        result = []
        for row_line in table_lines[2:]:
            if re.match(r'^[\s|:\-]+$', row_line):
                continue
            cells = [c.strip() for c in row_line.strip().strip('|').split('|')]
            cells = [c for c in cells if c or len(cells) <= len(headers) + 2]
            if len(headers) == 2:
                v0 = cells[0] if len(cells) > 0 else ""
                v1 = cells[1] if len(cells) > 1 else ""
                result.append(f"• **{v0}**  {v1}")
            else:
                parts = []
                for i, h in enumerate(headers):
                    c = cells[i] if i < len(cells) else ""
                    if c:
                        parts.append(f"**{h}**: {c}")
                result.append(f"• {' | '.join(parts)}")
        return '\n'.join(result)

    def _content_to_elements(self, content: str, use_v2_table: bool = True
                             ) -> tuple[list[dict], bool]:
        """解析内容为飞书卡片元素列表

        Returns: (elements, has_v2_tables)
        """
        lines = content.split('\n')
        segments: list[tuple] = []
        current_md: list[str] = []
        i = 0

        while i < len(lines):
            line = lines[i]
            if ('|' in line and i + 1 < len(lines)
                    and re.match(r'^[\s|:\-]+$', lines[i + 1])
                    and '|' in lines[i + 1]):
                if current_md:
                    segments.append(('md', '\n'.join(current_md)))
                    current_md = []
                headers, table_lines, j = self._parse_md_table(lines, i)
                segments.append(('table', (headers, table_lines)))
                i = j
                continue
            current_md.append(line)
            i += 1

        if current_md:
            segments.append(('md', '\n'.join(current_md)))

        elements: list[dict] = []
        table_count = 0
        has_v2_tables = False

        for seg_type, seg_data in segments:
            if seg_type == 'md':
                md_text = self._to_feishu_md(seg_data)
                sections = self._split_by_sections(md_text)
                for si, section in enumerate(sections):
                    for chunk in self._split_content(section, 2000):
                        elements.append({"tag": "markdown", "content": chunk})
                    if si < len(sections) - 1:
                        elements.append({"tag": "hr"})
            elif seg_type == 'table':
                headers, table_lines = seg_data
                if use_v2_table and table_count < 5:
                    elements.append(self._md_table_to_v2_element(headers, table_lines))
                    table_count += 1
                    has_v2_tables = True
                else:
                    bullets = self._md_table_to_bullets(headers, table_lines)
                    elements.append({"tag": "markdown", "content": bullets})

        return elements, has_v2_tables

    async def send_card(self, chat_id: str, title: str, content: str,
                        actions: list | None = None, mention_user_id: str = "",
                        mention_users: list[dict] | None = None):
        await self._ensure_token()
        from ..agent.memory import redact_secrets
        content = redact_secrets(content)

        if mention_users:
            mentions = " ".join(
                f'<at id={u["open_id"]}>{u.get("name", "")}</at>'
                for u in mention_users
            )
            content = f"{mentions}\n\n{content}"
        elif mention_user_id:
            content = f"<at id={mention_user_id}></at>\n\n{content}"

        elements, has_v2_tables = self._content_to_elements(content, use_v2_table=True)

        if actions:
            elements.append({"tag": "hr"})
            elements.append({"tag": "action", "actions": actions})

        theme = self._pick_header_theme(title)

        if has_v2_tables:
            card = {
                "schema": "2.0",
                "header": {
                    "template": theme,
                    "title": {"tag": "plain_text", "content": title},
                },
                "body": {
                    "elements": elements,
                },
            }
        else:
            card = {
                "header": {
                    "template": theme,
                    "title": {"tag": "plain_text", "content": title},
                },
                "elements": elements,
            }

        ok = await self._post_card(chat_id, card)

        if not ok and has_v2_tables:
            logger.warning("v2 table 卡片发送失败，降级为 v1 bullet 列表")
            elements_v1, _ = self._content_to_elements(content, use_v2_table=False)
            if actions:
                elements_v1.append({"tag": "hr"})
                elements_v1.append({"tag": "action", "actions": actions})
            card_v1 = {
                "header": {
                    "template": theme,
                    "title": {"tag": "plain_text", "content": title},
                },
                "elements": elements_v1,
            }
            ok = await self._post_card(chat_id, card_v1)

        if not ok:
            await self._send_text(chat_id, self._to_feishu_md(content))

    async def _post_card(self, chat_id: str, card: dict) -> bool:
        """发送卡片消息，成功返回 True"""
        payload = {
            "receive_id": chat_id,
            "msg_type": "interactive",
            "content": json.dumps(card),
        }
        headers = {
            "Authorization": f"Bearer {self._tenant_access_token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{FEISHU_API}/im/v1/messages",
                    params={"receive_id_type": "chat_id"},
                    json=payload, headers=headers,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("code", 0) == 0:
                        return True
                    logger.error(f"卡片业务错误: code={data.get('code')} msg={data.get('msg')}")
                else:
                    logger.error(f"卡片发送失败: {resp.status_code} {resp.text}")
                return False
        except Exception as e:
            logger.error(f"卡片发送异常: {e}")
            return False

    async def _send_text(self, chat_id: str, content: str):
        from ..agent.memory import redact_secrets
        content = redact_secrets(content)
        payload = {
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": content}),
        }
        headers = {
            "Authorization": f"Bearer {self._tenant_access_token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"{FEISHU_API}/im/v1/messages",
                    params={"receive_id_type": "chat_id"},
                    json=payload, headers=headers,
                )
        except Exception as e:
            logger.error(f"纯文本也失败了: {e}")

    async def send_message(self, content: str, **kwargs):
        chat_id = kwargs.get("chat_id", "")
        if chat_id:
            await self.send_card(chat_id, "🐦 灵雀", content)

    # ==================== 图片消息 ====================

    async def upload_image(self, image_path: str) -> str | None:
        """
        上传图片到飞书，返回 image_key
        
        支持格式: JPG, JPEG, PNG, WEBP, GIF, BMP, ICO, TIFF, HEIC
        限制: 不超过 10MB, GIF 分辨率 <= 2000x2000, 其他 <= 12000x12000
        """
        import os
        await self._ensure_token()

        if not os.path.exists(image_path):
            logger.error(f"图片文件不存在: {image_path}")
            return None

        # 根据扩展名确定正确的 MIME 类型
        ext = os.path.splitext(image_path)[1].lower()
        mime_map = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
            ".ico": "image/x-icon",
            ".tiff": "image/tiff", ".tif": "image/tiff",
            ".heic": "image/heic",
        }
        mime_type = mime_map.get(ext, "application/octet-stream")

        headers = {
            "Authorization": f"Bearer {self._tenant_access_token}",
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                with open(image_path, "rb") as f:
                    files = {"image": (os.path.basename(image_path), f, mime_type)}
                    data = {"image_type": "message"}
                    resp = await client.post(
                        f"{FEISHU_API}/im/v1/images",
                        headers=headers,
                        files=files,
                        data=data,
                    )

                if resp.status_code == 200:
                    result = resp.json()
                    if result.get("code") == 0:
                        image_key = result.get("data", {}).get("image_key")
                        logger.info(f"图片上传成功: {image_key}")
                        return image_key
                    else:
                        logger.error(f"图片上传失败: code={result.get('code')} msg={result.get('msg')}")
                else:
                    logger.error(f"图片上传HTTP失败: {resp.status_code}")

        except Exception as e:
            logger.error(f"上传图片异常: {e}")

        return None

    async def send_image(self, chat_id: str, image_path: str, caption: str = "") -> bool:
        """
        发送图片到飞书会话
        
        Args:
            chat_id: 会话 ID
            image_path: 本地图片路径
            caption: 图片说明（可选，会作为卡片标题）
            
        Returns:
            是否发送成功
        """
        image_key = await self.upload_image(image_path)
        if not image_key:
            return False

        await self._ensure_token()

        # 使用卡片消息发送图片（可以带标题）
        if caption:
            card = {
                "header": {
                    "template": "blue",
                    "title": {"tag": "plain_text", "content": caption},
                },
                "elements": [
                    {"tag": "img", "img_key": image_key, "alt": {"tag": "plain_text", "content": caption}},
                ],
            }
            payload = {
                "receive_id": chat_id,
                "msg_type": "interactive",
                "content": json.dumps(card),
            }
        else:
            # 纯图片消息
            payload = {
                "receive_id": chat_id,
                "msg_type": "image",
                "content": json.dumps({"image_key": image_key}),
            }

        headers = {
            "Authorization": f"Bearer {self._tenant_access_token}",
            "Content-Type": "application/json; charset=utf-8",
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{FEISHU_API}/im/v1/messages",
                    params={"receive_id_type": "chat_id"},
                    json=payload, headers=headers,
                )
                if resp.status_code == 200:
                    result = resp.json()
                    if result.get("code") == 0:
                        logger.info(f"图片发送成功: {chat_id}")
                        return True
                    else:
                        logger.error(f"图片发送失败: {result}")
                else:
                    logger.error(f"图片发送失败: {resp.status_code} {resp.text}")
        except Exception as e:
            logger.error(f"发送图片异常: {e}")

        return False

    def get_current_chat_id(self) -> str:
        """获取当前会话 ID（供技能使用，基于 contextvars 线程安全）"""
        return _active_chat_id.get()

    # ==================== 文件消息 ====================

    async def upload_file(self, file_path: str, file_type: str = "stream") -> str | None:
        """
        上传文件到飞书，返回 file_key
        
        支持类型: opus, mp4, pdf, doc, xls, ppt, stream
        限制: 不超过 30MB, 不允许上传空文件
        """
        import os
        await self._ensure_token()

        if not os.path.exists(file_path):
            logger.error(f"文件不存在: {file_path}")
            return None

        file_size = os.path.getsize(file_path)
        if file_size == 0:
            logger.error(f"不允许上传空文件: {file_path}")
            return None
        if file_size > 30 * 1024 * 1024:
            logger.error(f"文件超过 30MB 限制: {file_size} bytes")
            return None

        headers = {
            "Authorization": f"Bearer {self._tenant_access_token}",
        }

        # 根据扩展名自动判断 file_type (飞书 API 要求的枚举值)
        ext = os.path.splitext(file_path)[1].lower()
        type_map = {
            ".pdf": "pdf",
            ".doc": "doc", ".docx": "doc",
            ".xls": "xls", ".xlsx": "xls",
            ".ppt": "ppt", ".pptx": "ppt",
            ".mp4": "mp4",
            ".opus": "opus",
        }
        file_type = type_map.get(ext, "stream")

        # MIME 类型 (用于 multipart 上传)
        mime_map = {
            ".pdf": "application/pdf",
            ".doc": "application/msword",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".xls": "application/vnd.ms-excel",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".ppt": "application/vnd.ms-powerpoint",
            ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ".mp4": "video/mp4",
            ".opus": "audio/opus",
            ".txt": "text/plain",
            ".csv": "text/csv",
            ".json": "application/json",
            ".zip": "application/zip",
        }
        mime_type = mime_map.get(ext, "application/octet-stream")

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                with open(file_path, "rb") as f:
                    file_name = os.path.basename(file_path)
                    files = {"file": (file_name, f, mime_type)}
                    data = {"file_type": file_type, "file_name": file_name}
                    resp = await client.post(
                        f"{FEISHU_API}/im/v1/files",
                        headers=headers,
                        files=files,
                        data=data,
                    )

                if resp.status_code == 200:
                    result = resp.json()
                    if result.get("code") == 0:
                        file_key = result.get("data", {}).get("file_key")
                        logger.info(f"文件上传成功: {file_key}")
                        return file_key
                    else:
                        logger.error(f"文件上传失败: code={result.get('code')} msg={result.get('msg')}")
                else:
                    logger.error(f"文件上传HTTP失败: {resp.status_code}")

        except Exception as e:
            logger.error(f"上传文件异常: {e}")

        return None

    async def send_file(self, chat_id: str, file_path: str) -> bool:
        """
        发送文件到飞书会话
        
        Args:
            chat_id: 会话 ID
            file_path: 本地文件路径
            
        Returns:
            是否发送成功
        """
        file_key = await self.upload_file(file_path)
        if not file_key:
            return False

        await self._ensure_token()

        payload = {
            "receive_id": chat_id,
            "msg_type": "file",
            "content": json.dumps({"file_key": file_key}),
        }

        headers = {
            "Authorization": f"Bearer {self._tenant_access_token}",
            "Content-Type": "application/json; charset=utf-8",
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{FEISHU_API}/im/v1/messages",
                    params={"receive_id_type": "chat_id"},
                    json=payload, headers=headers,
                )
                if resp.status_code == 200:
                    result = resp.json()
                    if result.get("code") == 0:
                        logger.info(f"文件发送成功: {chat_id}")
                        return True
                    else:
                        logger.error(f"文件发送失败: {result}")
                else:
                    logger.error(f"文件发送失败: {resp.status_code} {resp.text}")
        except Exception as e:
            logger.error(f"发送文件异常: {e}")

        return False

    # ==================== 表情回复 ====================

    async def add_reaction(self, message_id: str, emoji_type: str = "THUMBSUP") -> str | None:
        """
        给消息添加表情回复
        
        Args:
            message_id: 消息 ID
            emoji_type: 表情类型 (如 SMILE, THUMBSUP, HEART, OK 等)
            
        Returns:
            reaction_id 或 None
        """
        await self._ensure_token()

        headers = {
            "Authorization": f"Bearer {self._tenant_access_token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        payload = {
            "reaction_type": {
                "emoji_type": emoji_type,
            }
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{FEISHU_API}/im/v1/messages/{message_id}/reactions",
                    json=payload,
                    headers=headers,
                )
                if resp.status_code == 200:
                    result = resp.json()
                    if result.get("code") == 0:
                        reaction_id = result.get("data", {}).get("reaction_id")
                        logger.info(f"表情回复添加成功: {emoji_type} -> {message_id}")
                        return reaction_id
                    else:
                        logger.error(f"表情回复失败: code={result.get('code')} msg={result.get('msg')}")
                else:
                    logger.error(f"表情回复HTTP失败: {resp.status_code}")
        except Exception as e:
            logger.error(f"添加表情回复异常: {e}")

        return None

    async def delete_reaction(self, message_id: str, reaction_id: str) -> bool:
        """
        删除消息的表情回复
        
        只能删除自己添加的表情回复
        """
        await self._ensure_token()

        headers = {
            "Authorization": f"Bearer {self._tenant_access_token}",
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.delete(
                    f"{FEISHU_API}/im/v1/messages/{message_id}/reactions/{reaction_id}",
                    headers=headers,
                )
                if resp.status_code == 200:
                    result = resp.json()
                    if result.get("code") == 0:
                        logger.info(f"表情回复删除成功: {reaction_id}")
                        return True
                    else:
                        logger.error(f"表情回复删除失败: code={result.get('code')} msg={result.get('msg')}")
                else:
                    logger.error(f"表情回复删除HTTP失败: {resp.status_code}")
        except Exception as e:
            logger.error(f"删除表情回复异常: {e}")

        return False

    async def get_reactions(self, message_id: str, emoji_type: str = "") -> list[dict]:
        """
        获取消息的表情回复列表
        
        Args:
            message_id: 消息 ID
            emoji_type: 可选，筛选特定表情类型
            
        Returns:
            表情回复列表
        """
        await self._ensure_token()

        headers = {
            "Authorization": f"Bearer {self._tenant_access_token}",
        }
        params = {"user_id_type": "open_id"}
        if emoji_type:
            params["reaction_type"] = emoji_type

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{FEISHU_API}/im/v1/messages/{message_id}/reactions",
                    headers=headers,
                    params=params,
                )
                if resp.status_code == 200:
                    result = resp.json()
                    if result.get("code") == 0:
                        items = result.get("data", {}).get("items", [])
                        logger.info(f"获取表情回复: {len(items)} 个")
                        return items
                    else:
                        logger.error(f"获取表情回复失败: code={result.get('code')} msg={result.get('msg')}")
                else:
                    logger.error(f"获取表情回复HTTP失败: {resp.status_code}")
        except Exception as e:
            logger.error(f"获取表情回复异常: {e}")

        return []

    async def react_to_last_message(self, chat_id: str, emoji_type: str = "THUMBSUP") -> bool:
        """
        给当前会话最后一条用户消息添加表情回复
        这是一个便捷方法，用于快速回应用户
        """
        state = self._get_chat_state(chat_id)
        last_msg_id = state.get("last_user_message_id", "")
        if not last_msg_id:
            logger.warning("没有可回复的用户消息")
            return False

        reaction_id = await self.add_reaction(last_msg_id, emoji_type)
        return reaction_id is not None

    # ==================== 执行进度 ====================

    async def _show_progress(self, step: int, total: int, skill_name: str, status: str):
        """显示执行进度给用户"""
        chat_id = _active_chat_id.get()
        if not chat_id:
            return

        # 技能友好名称
        friendly_names = {
            "browse_skill_market": "🛒 浏览技能市场",
            "scan_skill_market": "🔍 扫描技能市场",
            "web_search": "🔎 网络搜索",
            "fetch_webpage": "🌐 获取网页",
            "run_python": "🐍 执行 Python",
            "run_query": "🔍 查询系统信息",
            "run_command": "💻 执行系统命令",
            "read_file": "📖 读取文件",
            "write_file": "📝 写入文件",
            "list_directory": "📂 列出目录",
            "search_files": "🔍 搜索文件",
            "read_emails": "📧 读取邮件",
            "send_email": "📤 发送邮件",
            "list_calendar_events": "📅 查看日历",
            "create_calendar_event": "📅 创建日程",
            "memory_search": "🧠 搜索记忆",
            "browser_screenshot_and_send": "📸 截图并发送",
            "browser_open": "🌐 打开网页",
            "browser_click": "👆 点击元素",
            "browser_type": "⌨️ 输入文字",
            "browser_scroll": "📜 滚动页面",
            "browser_screenshot_send": "📸 截图发送",
            "browser_get_text": "📖 读取页面",
            "browser_close": "🔒 关闭浏览器",
        }

        display_name = friendly_names.get(skill_name, f"⚙️ {skill_name}")

        # 构建进度卡片
        progress_bar = "▓" * step + "░" * (total - step)
        content = f"{display_name}\n\n进度: [{progress_bar}] {step}/{total}"

        card = {
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": "🐦 灵雀正在工作..."},
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": content},
                },
            ],
        }

        try:
            state = self._get_chat_state(chat_id)
            if state.get("progress_message_id"):
                await self._update_card(state["progress_message_id"], card)
            else:
                state["progress_message_id"] = await self._send_interactive_card_with_id(
                    chat_id, card
                )
        except Exception as e:
            logger.warning(f"更新进度失败: {e}")

    async def _send_interactive_card_with_id(self, chat_id: str, card: dict) -> str | None:
        """发送交互式卡片并返回消息 ID"""
        await self._ensure_token()

        payload = {
            "receive_id": chat_id,
            "msg_type": "interactive",
            "content": json.dumps(card),
        }
        headers = {
            "Authorization": f"Bearer {self._tenant_access_token}",
            "Content-Type": "application/json; charset=utf-8",
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{FEISHU_API}/im/v1/messages",
                    params={"receive_id_type": "chat_id"},
                    json=payload, headers=headers,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("data", {}).get("message_id")
        except Exception as e:
            logger.error(f"发送卡片异常: {e}")
        return None

    async def _update_card(self, message_id: str, card: dict):
        """更新已发送的卡片消息"""
        await self._ensure_token()

        headers = {
            "Authorization": f"Bearer {self._tenant_access_token}",
            "Content-Type": "application/json; charset=utf-8",
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.patch(
                    f"{FEISHU_API}/im/v1/messages/{message_id}",
                    json={"content": json.dumps(card)},
                    headers=headers,
                )
        except Exception as e:
            logger.warning(f"更新卡片失败: {e}")

    def reset_progress(self):
        """重置进度状态（每次新对话开始时调用）"""
        chat_id = _active_chat_id.get()
        if chat_id and chat_id in self._chat_states:
            self._chat_states[chat_id]["progress_message_id"] = None
            self._chat_states[chat_id]["thinking_card_id"] = None

    # ==================== 按钮确认 ====================

    _HIGH_RISK_RE = re.compile(
        r'\b(rm|rmdir|del|unlink|shred|truncate|drop\s+|format\s)'
        r'|memory_clear|清空',
        re.IGNORECASE,
    )

    async def _ask_confirmation_via_card(self, description: str) -> bool:
        chat_id = _active_chat_id.get()
        if not chat_id:
            logger.warning("无活跃 chat_id, 自动放行")
            return True

        action_id = f"confirm_{uuid.uuid4().hex[:12]}"
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_confirmations[action_id] = future

        is_high_risk = bool(self._HIGH_RISK_RE.search(description))

        if is_high_risk:
            header_template = "red"
            header_title = "⚠️ 高风险操作"
            warning_note = "🚨 该操作可能导致数据丢失且不可撤销，请仔细确认！"
        else:
            header_template = "orange"
            header_title = "🔐 操作确认"
            warning_note = "⏱️ 请在 60 秒内确认，超时将自动取消"

        card = {
            "config": {"update_multi": True},
            "header": {
                "template": header_template,
                "title": {"tag": "plain_text", "content": header_title},
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": description,
                    },
                },
                {"tag": "hr"},
                {
                    "tag": "note",
                    "elements": [
                        {"tag": "plain_text", "content": warning_note},
                    ],
                },
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "✅ 确认执行"},
                            "type": "primary",
                            "value": {"action_id": action_id, "confirmed": "true"},
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "❌ 取消"},
                            "type": "danger",
                            "value": {"action_id": action_id, "confirmed": "false"},
                        },
                    ],
                },
            ],
        }

        await self._send_interactive_card(chat_id, card)

        try:
            return await asyncio.wait_for(future, timeout=60)
        except asyncio.TimeoutError:
            self._pending_confirmations.pop(action_id, None)
            await self.send_card(chat_id, "⏰ 超时", "操作已自动取消，如需执行请重新发起")
            return False

    async def _send_interactive_card(self, chat_id: str, card: dict):
        """发送交互式卡片"""
        await self._ensure_token()

        payload = {
            "receive_id": chat_id,
            "msg_type": "interactive",
            "content": json.dumps(card),
        }
        headers = {
            "Authorization": f"Bearer {self._tenant_access_token}",
            "Content-Type": "application/json; charset=utf-8",
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{FEISHU_API}/im/v1/messages",
                    params={"receive_id_type": "chat_id"},
                    json=payload, headers=headers,
                )
                if resp.status_code != 200:
                    logger.error(f"卡片发送失败: {resp.status_code} {resp.text}")
        except Exception as e:
            logger.error(f"发送卡片异常: {e}")

    async def ask_confirmation(self, description: str) -> bool:
        return await self._ask_confirmation_via_card(description)

    # ==================== 群成员管理 ====================

    _MEMBER_CACHE_TTL = 300  # 5 分钟

    async def get_group_members(self, chat_id: str) -> list[dict]:
        """
        获取飞书群成员列表（带缓存）。

        Returns: [{"open_id": "ou_xxx", "name": "张三"}, ...]
        """
        now = time.time()
        if chat_id in self._member_cache:
            cached_time, cached_members = self._member_cache[chat_id]
            if now - cached_time < self._MEMBER_CACHE_TTL:
                return cached_members

        await self._ensure_token()
        headers = {"Authorization": f"Bearer {self._tenant_access_token}"}
        members = []
        page_token = ""

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                while True:
                    params = {
                        "member_id_type": "open_id",
                        "page_size": 100,
                    }
                    if page_token:
                        params["page_token"] = page_token

                    resp = await client.get(
                        f"{FEISHU_API}/im/v1/chats/{chat_id}/members",
                        headers=headers, params=params,
                    )
                    data = resp.json()
                    if data.get("code") != 0:
                        logger.error(f"获取群成员失败: {data}")
                        break

                    items = data.get("data", {}).get("items", [])
                    for item in items:
                        members.append({
                            "open_id": item.get("member_id", ""),
                            "name": item.get("name", ""),
                        })

                    if not data.get("data", {}).get("has_more"):
                        break
                    page_token = data.get("data", {}).get("page_token", "")
                    if not page_token:
                        break

        except Exception as e:
            logger.error(f"获取群成员异常: {e}")

        seen = set()
        unique = []
        for m in members:
            if m["open_id"] not in seen:
                seen.add(m["open_id"])
                unique.append(m)

        self._member_cache[chat_id] = (now, unique)
        logger.info(f"群 {chat_id} 共 {len(unique)} 名成员")
        return unique

    async def find_member_by_name(self, chat_id: str, name: str) -> dict | None:
        """根据名字模糊查找群成员"""
        members = await self.get_group_members(chat_id)
        query = name.replace(" ", "").lower()

        for m in members:
            if m["name"].replace(" ", "").lower() == query:
                return m

        for m in members:
            m_name = m["name"].replace(" ", "").lower()
            if query in m_name or m_name in query:
                return m

        return None

    # ==================== 富文本解析 + 资源下载 ====================

    async def _parse_post_message(self, content: dict, msg_id: str) -> dict:
        """解析飞书富文本 (post) 消息，提取文字、图片、文件

        接收到的 post 结构为扁平格式:
            {"title": "...", "content": [[{tag, ...}, ...], ...]}
        at 标签的 user_id 是 @_user_N 占位符，真实用户在 message.mentions 中。
        """
        result: dict = {"text": "", "images": [], "files": []}

        title = content.get("title", "")
        paragraphs = content.get("content", [])

        texts: list[str] = []
        if title:
            texts.append(title)

        for paragraph in paragraphs:
            for element in paragraph:
                tag = element.get("tag", "")

                if tag == "text":
                    texts.append(element.get("text", ""))

                elif tag == "a":
                    texts.append(element.get("text", element.get("href", "")))

                elif tag == "img":
                    image_key = element.get("image_key", "")
                    if image_key:
                        b64 = await self._download_image_as_base64(msg_id, image_key)
                        if b64:
                            result["images"].append(b64)

                elif tag == "media":
                    file_key = element.get("file_key", "")
                    if file_key:
                        fpath = await self._download_file(msg_id, file_key, "video.mp4")
                        if fpath:
                            result["files"].append(fpath)

                elif tag == "code_block":
                    code_text = element.get("text", "")
                    if code_text:
                        lang = element.get("language", "")
                        texts.append(f"```{lang}\n{code_text}\n```")

                # at / emotion / hr 不提取文本

        result["text"] = " ".join(t for t in texts if t).strip()
        return result

    async def _download_file(self, msg_id: str, file_key: str, file_name: str) -> str:
        """通过 resource API 下载消息中的文件，保存到本地，返回路径"""
        await self._ensure_token()
        save_dir = self.config.workspace_dir / "uploads" / "files"
        save_dir.mkdir(parents=True, exist_ok=True)
        safe_name = f"{file_key[:12]}_{file_name}"
        save_path = save_dir / safe_name

        if save_path.exists():
            logger.info(f"文件已存在，跳过下载: {save_path}")
            return str(save_path)

        url = f"{FEISHU_API}/im/v1/messages/{msg_id}/resources/{file_key}"
        headers = {"Authorization": f"Bearer {self._tenant_access_token}"}
        params = {"type": "file"}

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.get(url, headers=headers, params=params)
                if resp.status_code == 200:
                    if len(resp.content) > 30 * 1024 * 1024:
                        logger.warning(f"文件过大 ({len(resp.content)} bytes)，跳过: {file_name}")
                        return ""
                    save_path.write_bytes(resp.content)
                    logger.info(f"文件下载成功: {file_name}, {len(resp.content)} bytes → {save_path}")
                    return str(save_path)
                logger.error(f"文件下载失败: HTTP {resp.status_code}, {resp.text[:200]}")
        except Exception as e:
            logger.error(f"文件下载异常: {e}")
        return ""

    async def _download_image_as_base64(self, message_id: str, image_key: str) -> str:
        """从飞书下载图片并转为 base64"""
        import base64
        await self._ensure_token()

        url = f"{FEISHU_API}/im/v1/messages/{message_id}/resources/{image_key}"
        headers = {"Authorization": f"Bearer {self._tenant_access_token}"}
        params = {"type": "image"}

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url, headers=headers, params=params)
                if resp.status_code == 200:
                    b64 = base64.b64encode(resp.content).decode("utf-8")
                    content_type = resp.headers.get("Content-Type", "image/png")
                    logger.info(f"图片下载成功: {image_key}, {len(resp.content)} bytes, {content_type}")
                    return b64
                else:
                    logger.error(f"图片下载失败: HTTP {resp.status_code}, {resp.text[:200]}")
                    return ""
        except Exception as e:
            logger.error(f"图片下载异常: {e}")
            return ""

    # ==================== Token 管理 ====================

    async def _ensure_token(self):
        if time.time() < self._token_expire_time - 300:
            return

        url = f"{FEISHU_API}/auth/v3/tenant_access_token/internal"
        payload = {"app_id": self.app_id, "app_secret": self.app_secret}

        retry_delay = 1
        for attempt in range(5):
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(url, json=payload)
                    data = resp.json()
                    if data.get("code") == 0:
                        self._tenant_access_token = data["tenant_access_token"]
                        self._token_expire_time = time.time() + data.get("expire", 7200)
                        logger.info(f"飞书 token 已刷新, {data.get('expire',7200)}s 后过期")
                        return
                    else:
                        logger.error(f"token 响应异常: {data}")
            except Exception as e:
                logger.error(f"token 刷新失败 (第{attempt+1}次): {e}")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30)

    # ==================== 工具方法 ====================

    @staticmethod
    def _split_by_sections(content: str) -> list[str]:
        """按逻辑段落拆分内容（以加粗标题行或连续空行为分界）"""
        import re
        lines = content.split('\n')
        sections = []
        current = []

        for line in lines:
            is_section_header = bool(re.match(r'^\*\*[^*]+\*\*\s*$', line.strip()))
            if is_section_header and current:
                text = '\n'.join(current).strip()
                if text:
                    sections.append(text)
                current = [line]
            else:
                current.append(line)

        if current:
            text = '\n'.join(current).strip()
            if text:
                sections.append(text)

        if len(sections) <= 1:
            return [content.strip()]
        return sections

    @staticmethod
    def _pick_header_theme(title: str) -> str:
        """根据标题选择卡片头主题色"""
        if any(k in title for k in ["错误", "失败", "超时"]):
            return "red"
        if any(k in title for k in ["成功", "完成", "已切换"]):
            return "green"
        if any(k in title for k in ["提醒", "通知", "警告"]):
            return "orange"
        if any(k in title for k in ["状态", "模型", "管理"]):
            return "indigo"
        return "blue"

    def _split_content(self, content: str, max_len: int = 2000) -> list[str]:
        if len(content) <= max_len:
            return [content]
        chunks, current = [], ""
        for line in content.split("\n"):
            if len(current) + len(line) + 1 > max_len:
                if current:
                    chunks.append(current)
                current = line
            else:
                current = current + "\n" + line if current else line
        if current:
            chunks.append(current)
        return chunks

    def _cleanup_msg_ids(self):
        now = time.time()
        expired = [k for k, v in self._processed_msg_ids.items() if now - v > self._msg_id_ttl]
        for k in expired:
            del self._processed_msg_ids[k]

    async def _health_check(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})
