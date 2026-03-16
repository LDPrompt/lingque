"""
🐦 OpenAI (GPT) Provider - 也兼容 DeepSeek 等 OpenAI 兼容 API

DeepSeek 特殊支持:
- deepseek-chat: 普通对话模式
- deepseek-reasoner: 思考模式，返回 reasoning_content
"""

import json
import logging
from openai import AsyncOpenAI
from .base import BaseLLMProvider, LLMResponse, ToolCall, Message

logger = logging.getLogger("lobster.llm")


class OpenAIProvider(BaseLLMProvider):

    def __init__(self, model: str, api_key: str, base_url: str | None = None,
                 fixed_temperature: float | None = None,
                 extra_body: dict | None = None, **kwargs):
        super().__init__(model, api_key)
        self.base_url = base_url
        self.fixed_temperature = fixed_temperature
        self.extra_body = extra_body
        client_kwargs = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        self.client = AsyncOpenAI(**client_kwargs)

    @property
    def is_deepseek(self) -> bool:
        return self.base_url and "deepseek" in self.base_url

    @property
    def is_reasoner(self) -> bool:
        return "reasoner" in self.model

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        system_prompt: str = "",
        temperature: float | None = None,
    ) -> LLMResponse:

        api_messages = []
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})

        for msg in messages:
            if msg.role == "system":
                continue
            if msg.role == "tool":
                api_messages.append({
                    "role": "tool",
                    "content": msg.content,
                    "tool_call_id": msg.tool_call_id,
                })
            elif msg.role == "assistant" and msg.tool_calls:
                assistant_msg = {
                    "role": "assistant",
                    "content": msg.content or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                }
                if msg.reasoning_content or self.extra_body:
                    assistant_msg["reasoning_content"] = msg.reasoning_content or ""
                api_messages.append(assistant_msg)
            elif msg.role == "user" and msg.images:
                content_parts = []
                if msg.content:
                    content_parts.append({"type": "text", "text": msg.content})
                for img_b64 in msg.images:
                    if img_b64.startswith("data:"):
                        image_url = img_b64
                    else:
                        image_url = f"data:image/png;base64,{img_b64}"
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": image_url},
                    })
                api_messages.append({"role": "user", "content": content_parts})
            else:
                api_messages.append({"role": msg.role, "content": msg.content})

        if self.fixed_temperature is not None:
            temperature = self.fixed_temperature
        elif self.extra_body:
            temperature = None
        elif temperature is None:
            if self.is_deepseek:
                temperature = 0.0 if tools else 1.0
            else:
                temperature = 0.7

        kwargs = {
            "model": self.model,
            "messages": api_messages,
            "max_tokens": 8192,
        }

        if temperature is not None:
            kwargs["temperature"] = temperature

        if self.extra_body:
            kwargs["extra_body"] = self.extra_body
            logger.debug(f"extra_body 已注入: {self.extra_body}")

        # DeepSeek reasoner 不支持 tools
        if tools and not self.is_reasoner:
            kwargs["tools"] = self.format_tools(tools)

        response = await self.client.chat.completions.create(**kwargs)
        if not response.choices:
            return LLMResponse(content="", stop_reason="empty_response",
                               usage={"input_tokens": 0, "output_tokens": 0})
        choice = response.choices[0]

        content = choice.message.content or ""

        reasoning_content = getattr(choice.message, "reasoning_content", "") or ""

        # MiniMax 兼容: reasoning_details 字段（reasoning_split=True 时返回）
        if not reasoning_content:
            reasoning_details = getattr(choice.message, "reasoning_details", None)
            if reasoning_details and isinstance(reasoning_details, list):
                parts = [d.get("text", "") for d in reasoning_details if isinstance(d, dict) and d.get("text")]
                if parts:
                    reasoning_content = "\n".join(parts)

        # 解析工具调用
        tool_calls = []
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                args = self._safe_parse_arguments(tc.function.arguments)
                if args is not None:
                    tool_calls.append(
                        ToolCall(
                            id=tc.id,
                            name=tc.function.name,
                            arguments=args,
                        )
                    )
                else:
                    logger.warning(
                        f"跳过无法解析的工具调用: {tc.function.name}, "
                        f"arguments 被截断: {tc.function.arguments[:100]}..."
                    )

        # 检测截断：finish_reason=length 说明输出被 max_tokens 截断
        _tool_calls_truncated = False
        if choice.finish_reason == "length" and choice.message.tool_calls:
            _tool_calls_truncated = True
            logger.warning(
                f"输出被截断 (finish_reason=length)，"
                f"成功解析 {len(tool_calls)}/{len(choice.message.tool_calls)} 个工具调用"
            )
            # 即使部分解析成功，截断意味着参数可能不完整，全部丢弃更安全
            tool_calls = []

        # 如果 LLM 本来想调工具，但全部解析失败，也标记为截断
        if choice.message.tool_calls and not tool_calls and not _tool_calls_truncated:
            _tool_calls_truncated = True
            logger.warning("所有工具调用参数解析失败，将由 Agent 循环重试")

        # 统计 token 用量 (DeepSeek reasoner 有额外的 reasoning_tokens)
        usage = {
            "input_tokens": response.usage.prompt_tokens if response.usage else 0,
            "output_tokens": response.usage.completion_tokens if response.usage else 0,
        }
        if response.usage and hasattr(response.usage, "completion_tokens_details"):
            details = response.usage.completion_tokens_details
            if details and hasattr(details, "reasoning_tokens"):
                usage["reasoning_tokens"] = details.reasoning_tokens or 0

        resp = LLMResponse(
            content=content,
            tool_calls=tool_calls,
            usage=usage,
            stop_reason=choice.finish_reason or "",
            reasoning_content=reasoning_content,
            raw=response,
        )
        resp.tool_calls_truncated = _tool_calls_truncated
        return resp

    @staticmethod
    def _safe_parse_arguments(raw: str) -> dict | None:
        """安全解析工具调用参数，处理 DeepSeek 截断 JSON 的情况"""
        if not raw or not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        repaired = raw.rstrip()

        # 策略 1：补全尾部缺失的引号和括号
        open_quotes = repaired.count('"') % 2
        if open_quotes:
            repaired += '"'
        open_braces = repaired.count('{') - repaired.count('}')
        open_brackets = repaired.count('[') - repaired.count(']')
        repaired += ']' * max(open_brackets, 0)
        repaired += '}' * max(open_braces, 0)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

        # 策略 2：截断到最后一个完整的 key-value 对
        # 适用于 {"code": "very long string that got cut...
        last_complete = raw.rfind('",')
        if last_complete > 0:
            truncated = raw[:last_complete + 1]
            open_braces = truncated.count('{') - truncated.count('}')
            truncated += '}' * max(open_braces, 0)
            try:
                return json.loads(truncated)
            except json.JSONDecodeError:
                pass

        # 策略 3：只有一个 key 的情况（如 {"code": "...截断）
        # 找到第一个 ": " 后的值部分，截断补全
        import re
        m = re.match(r'\{\s*"(\w+)"\s*:\s*"', raw)
        if m:
            key = m.group(1)
            value_start = m.end()
            value_content = raw[value_start:]
            value_content = value_content.replace('\\', '\\\\').rstrip()
            if value_content.endswith('"'):
                value_content = value_content[:-1]
            # 取到截断前的内容，作为部分值返回
            clean_value = raw[value_start:].rstrip().rstrip('"')
            try:
                parsed = json.loads(f'{{"{key}": {json.dumps(clean_value)}}}')
                logger.warning(f"JSON 截断修复: 保留部分 {key} 值 ({len(clean_value)} 字符)")
                return parsed
            except Exception:
                pass

        logger.error(f"JSON 修复失败，原始内容: {raw[:200]}")
        return None

    def format_tools(self, tools: list[dict]) -> list[dict]:
        """转换为 OpenAI function calling 格式"""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
                },
            }
            for tool in tools
        ]
