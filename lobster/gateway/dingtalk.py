"""
🐦 钉钉通道适配器
接收钉钉机器人消息 → Agent 处理 → 回复

需要:
1. 在钉钉开放平台创建企业内部应用
2. 启用机器人能力
3. 配置消息接收地址 (HTTP)
"""

import json
import asyncio
import logging
import hashlib
import hmac
import base64
import time
from aiohttp import web
import httpx

from .base import BaseChannel
from ..agent.core import Agent

logger = logging.getLogger("lobster.dingtalk")


class DingTalkChannel(BaseChannel):
    """钉钉消息通道"""

    def __init__(self, agent: Agent, config):
        super().__init__(agent)
        self.app_key = config.get("app_key", "")
        self.app_secret = config.get("app_secret", "")
        self._access_token = ""
        self.agent.set_confirm_callback(self.ask_confirmation)

    async def start(self, host: str = "0.0.0.0", port: int = 9001):
        await self._refresh_token()

        app = web.Application()
        app.router.add_post("/webhook/dingtalk", self._handle_webhook)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        logger.info(f"钉钉 Webhook 服务器已启动: http://{host}:{port}/webhook/dingtalk")

        while True:
            await asyncio.sleep(3600)
            await self._refresh_token()

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.Response(status=400)

        asyncio.create_task(self._handle_message(body))
        return web.Response(status=200, text="ok")

    async def _handle_message(self, body: dict):
        try:
            msg_type = body.get("msgtype", "")
            if msg_type != "text":
                return

            text = body.get("text", {}).get("content", "").strip()
            webhook_url = body.get("sessionWebhook", "")

            if not text or not webhook_url:
                return

            logger.info(f"收到钉钉消息: {text[:100]}")
            response = await self.agent.process_message(text)

            # 通过 session webhook 回复
            async with httpx.AsyncClient() as client:
                await client.post(
                    webhook_url,
                    json={"msgtype": "text", "text": {"content": response}},
                )
        except Exception as e:
            logger.error(f"处理钉钉消息失败: {e}", exc_info=True)

    async def send_message(self, content: str, **kwargs):
        webhook_url = kwargs.get("webhook_url", "")
        if webhook_url:
            async with httpx.AsyncClient() as client:
                await client.post(
                    webhook_url,
                    json={"msgtype": "text", "text": {"content": content}},
                )

    async def ask_confirmation(self, description: str) -> bool:
        logger.warning(f"高风险操作已拒绝（钉钉渠道暂未实现确认流程）: {description}")
        return False

    async def _refresh_token(self):
        url = "https://oapi.dingtalk.com/gettoken"
        params = {"appkey": self.app_key, "appsecret": self.app_secret}
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, params=params)
                data = resp.json()
                self._access_token = data.get("access_token", "")
                logger.info("钉钉 token 刷新成功")
        except Exception as e:
            logger.error(f"钉钉 token 刷新失败: {e}")
