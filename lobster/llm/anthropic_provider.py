"""
🐦 Anthropic (Claude) Provider
"""

import json
import anthropic
from .base import BaseLLMProvider, LLMResponse, ToolCall, Message


class AnthropicProvider(BaseLLMProvider):

    def __init__(self, model: str, api_key: str, **kwargs):
        super().__init__(model, api_key)
        self.client = anthropic.AsyncAnthropic(api_key=api_key)

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        system_prompt: str = "",
        temperature: float | None = None,
    ) -> LLMResponse:
        if temperature is None:
            temperature = 0.7

        # 构建消息列表 (Anthropic 格式)
        api_messages = []
        for msg in messages:
            if msg.role == "system":
                continue  # system 走单独参数

            if msg.role == "tool":
                # 工具结果 → Anthropic 的 tool_result 格式
                api_messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.tool_call_id,
                            "content": msg.content,
                        }
                    ],
                })
            elif msg.role == "assistant" and msg.tool_calls:
                content_blocks = []
                if msg.content:
                    content_blocks.append({"type": "text", "text": msg.content})
                for tc in msg.tool_calls:
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    })
                api_messages.append({"role": "assistant", "content": content_blocks})
            elif msg.role == "user" and msg.images:
                content_parts = []
                if msg.content:
                    content_parts.append({"type": "text", "text": msg.content})
                for img_b64 in msg.images:
                    if img_b64.startswith("data:"):
                        media_type = img_b64.split(";")[0].split(":")[1]
                        b64_data = img_b64.split(",", 1)[1]
                    else:
                        media_type = "image/png"
                        b64_data = img_b64
                    content_parts.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": b64_data},
                    })
                api_messages.append({"role": "user", "content": content_parts})
            else:
                api_messages.append({"role": msg.role, "content": msg.content})

        # 调用 API
        kwargs = {
            "model": self.model,
            "max_tokens": self.max_output_tokens if self.max_output_tokens > 0 else 16384,
            "messages": api_messages,
            "temperature": temperature,
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        if tools:
            kwargs["tools"] = self.format_tools(tools)

        response = await self.client.messages.create(**kwargs)

        # 解析响应
        content = ""
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=dict(block.input) if block.input else {},
                    )
                )

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            usage={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
            stop_reason=response.stop_reason,
            raw=response,
        )

    def format_tools(self, tools: list[dict]) -> list[dict]:
        """转换为 Anthropic tool 格式"""
        formatted = []
        for tool in tools:
            formatted.append({
                "name": tool["name"],
                "description": tool.get("description", ""),
                "input_schema": tool.get("parameters", {"type": "object", "properties": {}}),
            })
        return formatted
