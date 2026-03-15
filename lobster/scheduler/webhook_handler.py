"""
🐦 灵雀 - Webhook 接入 (P2)

功能:
- 接收外部事件 (GitHub, Sentry, 自定义等)
- 解析事件并触发 Agent 任务
- 支持多种事件类型
"""

import logging
import hashlib
import hmac
import json
from typing import Callable, Any
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger("lingque.webhook")


@dataclass
class WebhookEvent:
    """Webhook 事件"""
    source: str  # github, sentry, custom 等
    event_type: str
    payload: dict
    timestamp: datetime
    signature: str = ""


class WebhookHandler:
    """
    Webhook 事件处理器

    功能:
    - 接收并验证 Webhook 请求
    - 解析 GitHub/Sentry 等常见格式
    - 触发 Agent 处理或自定义回调

    用法:
        handler = WebhookHandler(agent, notify_callback)
        handler.set_github_secret("your_secret")
        await handler.handle(request_body, headers)
    """

    def __init__(
        self,
        agent=None,
        notify_callback: Callable = None,
        notify_chat_id: str = "",
    ):
        self.agent = agent
        self.notify_callback = notify_callback
        self.notify_chat_id = notify_chat_id

        # 各平台的验证密钥
        self._secrets = {
            "github": "",
            "sentry": "",
            "custom": "",
        }

        # 自定义事件处理器
        self._handlers: dict[str, Callable] = {}

    def set_secret(self, source: str, secret: str):
        """设置平台的验证密钥"""
        self._secrets[source] = secret

    def register_handler(self, source: str, event_type: str, handler: Callable):
        """注册自定义事件处理器"""
        key = f"{source}:{event_type}"
        self._handlers[key] = handler
        logger.info(f"已注册事件处理器: {key}")

    async def handle(
        self,
        body: bytes | dict,
        headers: dict,
        source: str = "auto"
    ) -> dict:
        """
        处理 Webhook 请求

        Args:
            body: 请求体
            headers: 请求头
            source: 来源 (auto=自动检测)

        Returns:
            处理结果
        """
        # 自动检测来源
        if source == "auto":
            source = self._detect_source(headers)

        # 解析请求体
        if isinstance(body, bytes):
            try:
                payload = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                return {"error": "Invalid JSON"}
        else:
            payload = body

        # 验证签名
        if not self._verify_signature(source, body, headers):
            logger.warning(f"Webhook 签名验证失败: {source}")
            return {"error": "Invalid signature"}

        # 解析事件
        event = self._parse_event(source, payload, headers)
        logger.info(f"收到 Webhook 事件: {source}/{event.event_type}")

        # 处理事件
        try:
            result = await self._process_event(event)
            return {"status": "ok", "result": result}
        except Exception as e:
            logger.error(f"处理 Webhook 事件失败: {e}", exc_info=True)
            return {"error": "Internal processing error"}

    def _detect_source(self, headers: dict) -> str:
        """自动检测 Webhook 来源"""
        headers_lower = {k.lower(): v for k, v in headers.items()}

        if "x-github-event" in headers_lower:
            return "github"
        elif "sentry-hook-resource" in headers_lower:
            return "sentry"
        elif "x-gitlab-event" in headers_lower:
            return "gitlab"
        else:
            return "custom"

    def _verify_signature(
        self,
        source: str,
        body: bytes | dict,
        headers: dict
    ) -> bool:
        """验证 Webhook 签名"""
        secret = self._secrets.get(source, "")
        if not secret:
            logger.warning(f"Webhook 来源 '{source}' 未配置签名密钥，拒绝请求（安全策略）")
            return False

        if isinstance(body, dict):
            body = json.dumps(body, separators=(",", ":")).encode("utf-8")

        headers_lower = {k.lower(): v for k, v in headers.items()}

        if source == "github":
            sig = headers_lower.get("x-hub-signature-256", "")
            expected = "sha256=" + hmac.new(
                secret.encode(),
                body,
                hashlib.sha256
            ).hexdigest()
            return hmac.compare_digest(sig, expected)

        elif source == "sentry":
            logger.warning("Sentry 签名验证尚未实现，已拒绝请求")
            return False

        logger.warning(f"未知 Webhook 来源 '{source}' 配置了密钥但无对应验证逻辑，已拒绝")
        return False

    def _parse_event(
        self,
        source: str,
        payload: dict,
        headers: dict
    ) -> WebhookEvent:
        """解析 Webhook 事件"""
        headers_lower = {k.lower(): v for k, v in headers.items()}

        if source == "github":
            event_type = headers_lower.get("x-github-event", "unknown")
        elif source == "sentry":
            event_type = headers_lower.get("sentry-hook-resource", "unknown")
        elif source == "gitlab":
            event_type = headers_lower.get("x-gitlab-event", "unknown")
        else:
            event_type = payload.get("event", "custom")

        return WebhookEvent(
            source=source,
            event_type=event_type,
            payload=payload,
            timestamp=datetime.now(),
        )

    async def _process_event(self, event: WebhookEvent) -> str:
        """处理事件"""
        # 检查是否有自定义处理器
        handler_key = f"{event.source}:{event.event_type}"
        if handler_key in self._handlers:
            return await self._handlers[handler_key](event)

        # 根据来源和事件类型生成消息
        message = self._format_event_message(event)

        # 发送通知
        if self.notify_callback and self.notify_chat_id:
            await self.notify_callback(self.notify_chat_id, message)

        # 如果配置了 Agent，让 Agent 处理
        if self.agent:
            prompt = f"收到一个 {event.source} 的 {event.event_type} 事件，请分析并给出建议:\n\n{message}"
            return await self.agent.process_message(prompt)

        return message

    def _format_event_message(self, event: WebhookEvent) -> str:
        """格式化事件消息"""
        if event.source == "github":
            return self._format_github_event(event)
        elif event.source == "sentry":
            return self._format_sentry_event(event)
        else:
            return self._format_generic_event(event)

    def _format_github_event(self, event: WebhookEvent) -> str:
        """格式化 GitHub 事件"""
        payload = event.payload
        event_type = event.event_type

        if event_type == "push":
            repo = payload.get("repository", {}).get("full_name", "")
            pusher = payload.get("pusher", {}).get("name", "")
            commits = payload.get("commits", [])
            ref = payload.get("ref", "").replace("refs/heads/", "")

            commit_msgs = "\n".join(
                f"  • {c.get('message', '').split(chr(10))[0]}"
                for c in commits[:5]
            )
            return (
                f"🔔 **GitHub Push**\n\n"
                f"**仓库**: {repo}\n"
                f"**分支**: {ref}\n"
                f"**推送者**: {pusher}\n"
                f"**提交数**: {len(commits)}\n\n"
                f"**提交信息**:\n{commit_msgs}"
            )

        elif event_type == "pull_request":
            action = payload.get("action", "")
            pr = payload.get("pull_request", {})
            repo = payload.get("repository", {}).get("full_name", "")
            title = pr.get("title", "")
            user = pr.get("user", {}).get("login", "")
            url = pr.get("html_url", "")

            return (
                f"🔔 **GitHub PR {action}**\n\n"
                f"**仓库**: {repo}\n"
                f"**标题**: {title}\n"
                f"**作者**: {user}\n"
                f"**链接**: {url}"
            )

        elif event_type == "issues":
            action = payload.get("action", "")
            issue = payload.get("issue", {})
            repo = payload.get("repository", {}).get("full_name", "")
            title = issue.get("title", "")
            user = issue.get("user", {}).get("login", "")

            return (
                f"🔔 **GitHub Issue {action}**\n\n"
                f"**仓库**: {repo}\n"
                f"**标题**: {title}\n"
                f"**作者**: {user}"
            )

        else:
            return f"🔔 **GitHub {event_type}** 事件\n\n```json\n{json.dumps(payload, indent=2)[:1000]}\n```"

    def _format_sentry_event(self, event: WebhookEvent) -> str:
        """格式化 Sentry 事件"""
        payload = event.payload
        event_type = event.event_type

        if event_type == "error":
            title = payload.get("title", "Unknown Error")
            project = payload.get("project", "")
            url = payload.get("url", "")
            level = payload.get("level", "error")

            return (
                f"🚨 **Sentry {level.upper()}**\n\n"
                f"**项目**: {project}\n"
                f"**错误**: {title}\n"
                f"**链接**: {url}"
            )

        return f"🚨 **Sentry {event_type}** 事件"

    def _format_generic_event(self, event: WebhookEvent) -> str:
        """格式化通用事件（过滤敏感字段）"""
        _SENSITIVE_KEYS = {"token", "secret", "password", "authorization", "api_key", "apikey", "access_token"}
        def _redact(obj, depth=0):
            if depth > 5:
                return "..."
            if isinstance(obj, dict):
                return {k: ("***" if k.lower() in _SENSITIVE_KEYS else _redact(v, depth + 1))
                        for k, v in obj.items()}
            if isinstance(obj, list):
                return [_redact(i, depth + 1) for i in obj[:20]]
            return obj
        safe_payload = _redact(event.payload)
        preview = json.dumps(safe_payload, indent=2, ensure_ascii=False)[:500]
        return (
            f"🔔 **Webhook 事件**\n\n"
            f"**来源**: {event.source}\n"
            f"**类型**: {event.event_type}\n"
            f"**时间**: {event.timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"```json\n{preview}\n```"
        )
