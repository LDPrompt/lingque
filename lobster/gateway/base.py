"""
🐦 Channel 基类
所有消息通道（飞书/钉钉/CLI）都实现这个接口
"""

from abc import ABC, abstractmethod
from ..agent.core import Agent


class BaseChannel(ABC):
    """消息通道基类"""

    def __init__(self, agent: Agent):
        self.agent = agent

    @abstractmethod
    async def start(self):
        """启动通道，开始监听消息"""
        ...

    @abstractmethod
    async def send_message(self, content: str, **kwargs):
        """发送消息给用户"""
        ...

    @abstractmethod
    async def ask_confirmation(self, description: str) -> bool:
        """请求用户确认高风险操作"""
        ...
