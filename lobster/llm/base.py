"""
🐦 LLM Provider 基类
定义统一的模型调用接口，所有 Provider 都实现这个接口
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    """模型返回的工具调用请求"""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """模型响应的统一格式"""
    content: str = ""                          # 文本回复
    tool_calls: list[ToolCall] = field(default_factory=list)  # 工具调用
    usage: dict[str, int] = field(default_factory=dict)       # token 用量
    stop_reason: str = ""                      # 停止原因
    reasoning_content: str = ""                # 思考过程 (DeepSeek reasoner)
    raw: Any = None                            # 原始响应（调试用）
    tool_calls_truncated: bool = False         # 工具调用参数被截断


@dataclass
class Message:
    """对话消息"""
    role: str       # "user" | "assistant" | "system" | "tool"
    content: str
    name: str = ""           # 工具名
    tool_call_id: str = ""   # 工具调用 ID
    tool_calls: list[ToolCall] = field(default_factory=list)
    images: list[str] = field(default_factory=list)  # base64 编码的图片列表
    reasoning_content: str = ""  # 思考过程 (Kimi k2.5 / DeepSeek reasoner)
    _is_intervention: bool = False


class BaseLLMProvider(ABC):
    """LLM Provider 基类"""

    def __init__(self, model: str, api_key: str, **kwargs):
        self.model = model
        self.api_key = api_key

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        system_prompt: str = "",
        temperature: float | None = None,
    ) -> LLMResponse:
        """发送对话请求，temperature 为 None 时由 Provider 自动选择"""
        ...

    @abstractmethod
    def format_tools(self, tools: list[dict]) -> list[dict]:
        """将统一的工具定义转换为该 Provider 的格式"""
        ...
