"""
🐦 灵雀 - 流式输出支持
为 LLM Provider 添加 stream_chat 方法, 边生成边发送
"""

import json
import logging
from typing import AsyncIterator
from ..llm.base import BaseLLMProvider, LLMResponse, ToolCall, Message

logger = logging.getLogger("lingque.streaming")


class StreamChunk:
    """流式输出的一个片段"""
    def __init__(self, text: str = "", tool_call: ToolCall | None = None, done: bool = False):
        self.text = text
        self.tool_call = tool_call
        self.done = done


async def stream_anthropic(provider, messages, tools=None, system_prompt="", temperature=0.7) -> AsyncIterator[StreamChunk]:
    """Anthropic 流式输出"""
    import anthropic

    # 构建消息 (复用 provider 的格式化逻辑)
    api_messages = []
    for msg in messages:
        if msg.role == "system":
            continue
        if msg.role == "tool":
            api_messages.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": msg.tool_call_id, "content": msg.content}],
            })
        elif msg.role == "assistant" and msg.tool_calls:
            blocks = []
            if msg.content:
                blocks.append({"type": "text", "text": msg.content})
            for tc in msg.tool_calls:
                blocks.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments})
            api_messages.append({"role": "assistant", "content": blocks})
        else:
            api_messages.append({"role": msg.role, "content": msg.content})

    _max_out = provider.max_output_tokens if provider.max_output_tokens > 0 else 16384
    kwargs = {"model": provider.model, "max_tokens": _max_out, "messages": api_messages, "temperature": temperature}
    if system_prompt:
        kwargs["system"] = system_prompt
    if tools:
        kwargs["tools"] = provider.format_tools(tools)

    # 流式请求
    async with provider.client.messages.stream(**kwargs) as stream:
        current_tool = None
        tool_json = ""

        async for event in stream:
            if hasattr(event, 'type'):
                if event.type == 'content_block_start':
                    block = event.content_block
                    if hasattr(block, 'type') and block.type == 'tool_use':
                        current_tool = {"id": block.id, "name": block.name}
                        tool_json = ""
                elif event.type == 'content_block_delta':
                    delta = event.delta
                    if hasattr(delta, 'text'):
                        yield StreamChunk(text=delta.text)
                    elif hasattr(delta, 'partial_json'):
                        tool_json += delta.partial_json
                elif event.type == 'content_block_stop':
                    if current_tool:
                        try:
                            args = json.loads(tool_json) if tool_json else {}
                        except json.JSONDecodeError:
                            args = {}
                        yield StreamChunk(tool_call=ToolCall(
                            id=current_tool["id"], name=current_tool["name"], arguments=args
                        ))
                        current_tool = None
                        tool_json = ""

    yield StreamChunk(done=True)


async def stream_openai(provider, messages, tools=None, system_prompt="", temperature=0.7) -> AsyncIterator[StreamChunk]:
    """OpenAI 兼容的流式输出 (也适用于 DeepSeek)"""

    api_messages = []
    if system_prompt:
        api_messages.append({"role": "system", "content": system_prompt})
    for msg in messages:
        if msg.role == "system":
            continue
        if msg.role == "tool":
            api_messages.append({"role": "tool", "content": msg.content, "tool_call_id": msg.tool_call_id})
        elif msg.role == "assistant" and msg.tool_calls:
            api_messages.append({
                "role": "assistant", "content": msg.content or None,
                "tool_calls": [{"id": tc.id, "type": "function",
                                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                               for tc in msg.tool_calls],
            })
        else:
            api_messages.append({"role": msg.role, "content": msg.content})

    kwargs = {"model": provider.model, "messages": api_messages, "temperature": temperature, "stream": True}
    if tools:
        kwargs["tools"] = provider.format_tools(tools)

    tool_calls_buffer: dict[int, dict] = {}  # index → {id, name, args_str}

    async for chunk in await provider.client.chat.completions.create(**kwargs):
        delta = chunk.choices[0].delta if chunk.choices else None
        if not delta:
            continue

        # 文本片段
        if delta.content:
            yield StreamChunk(text=delta.content)

        # 工具调用片段
        if delta.tool_calls:
            for tc_delta in delta.tool_calls:
                idx = tc_delta.index
                if idx not in tool_calls_buffer:
                    tool_calls_buffer[idx] = {"id": "", "name": "", "args": ""}
                if tc_delta.id:
                    tool_calls_buffer[idx]["id"] = tc_delta.id
                if tc_delta.function:
                    if tc_delta.function.name:
                        tool_calls_buffer[idx]["name"] = tc_delta.function.name
                    if tc_delta.function.arguments:
                        tool_calls_buffer[idx]["args"] += tc_delta.function.arguments

        # 结束
        if chunk.choices[0].finish_reason:
            for buf in tool_calls_buffer.values():
                try:
                    args = json.loads(buf["args"]) if buf["args"] else {}
                except json.JSONDecodeError:
                    args = {}
                yield StreamChunk(tool_call=ToolCall(id=buf["id"], name=buf["name"], arguments=args))
            break

    yield StreamChunk(done=True)
