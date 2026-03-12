"""
🐦 灵雀 - 可插拔上下文管理引擎

升级特性：
- 重要消息标记：关键消息永不被压缩
- 滑动窗口压缩：分段智能压缩
- 消息优先级：根据重要性决定保留

Lifecycle Hooks:
- bootstrap(): 会话开始时初始化上下文
- ingest(message): 注入新消息到上下文
- assemble(): 组装最终发送给 LLM 的上下文
- compact(llm): 压缩/裁剪上下文（当超出 token 限制时）
- after_turn(response): 对话轮次结束后的处理
- prepare_subagent(task): 为子 Agent 准备上下文

使用方式:
1. 继承 BaseContextEngine 实现自定义策略
2. 在 Agent 初始化时注入自定义 ContextEngine
3. 或使用默认的 DefaultContextEngine
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING, Set
from enum import Enum
import logging

if TYPE_CHECKING:
    from ..llm import LLMRouter, Message

logger = logging.getLogger("lingque.context_engine")


class MessagePriority(Enum):
    """消息优先级"""
    CRITICAL = 3   # 关键消息，永不压缩
    HIGH = 2       # 高优先级，尽量保留
    NORMAL = 1     # 普通消息
    LOW = 0        # 低优先级，优先压缩


@dataclass
class ContextConfig:
    """上下文配置"""
    max_messages: int = 80          # 最大消息条数
    max_tokens: int = 64000         # 最大 token 数
    compression_threshold: float = 0.8  # 触发压缩的阈值（超过 max_tokens 的 80%）
    keep_system_prompt: bool = True     # 压缩时保留系统提示
    keep_recent_messages: int = 10      # 压缩时保留最近 N 条消息
    sliding_window_size: int = 20       # 滑动窗口大小（分段压缩）
    auto_mark_important: bool = True    # 自动标记重要消息


class BaseContextEngine(ABC):
    """
    上下文管理引擎基类 v2.0
    
    升级特性：
    - 重要消息标记
    - 消息优先级管理
    
    所有自定义上下文策略都应继承此类并实现相应的 hooks。
    """
    
    def __init__(self, config: Optional[ContextConfig] = None):
        self.config = config or ContextConfig()
        self._messages: list["Message"] = []
        self._system_prompt: str = ""
        self._metadata: dict = {}  # 额外元数据
        self._important_indices: Set[int] = set()  # 标记为重要的消息索引
        self._message_priorities: dict[int, MessagePriority] = {}  # 消息优先级
    
    @property
    def messages(self) -> list["Message"]:
        return self._messages
    
    @messages.setter
    def messages(self, value: list["Message"]):
        self._messages = value
        # 重置标记（索引可能变化）
        self._important_indices.clear()
        self._message_priorities.clear()
    
    # ==================== 重要消息管理 ====================
    
    def mark_important(self, index: int, priority: MessagePriority = MessagePriority.HIGH) -> None:
        """
        标记消息为重要（压缩时保留）
        
        Args:
            index: 消息索引（支持负数索引）
            priority: 优先级
        """
        if index < 0:
            index = len(self._messages) + index
        if 0 <= index < len(self._messages):
            self._important_indices.add(index)
            self._message_priorities[index] = priority
            logger.debug(f"标记消息 #{index} 为重要 (优先级: {priority.name})")
    
    def unmark_important(self, index: int) -> None:
        """取消重要标记"""
        if index < 0:
            index = len(self._messages) + index
        self._important_indices.discard(index)
        self._message_priorities.pop(index, None)
    
    def is_important(self, index: int) -> bool:
        """检查消息是否被标记为重要"""
        if index < 0:
            index = len(self._messages) + index
        return index in self._important_indices
    
    def get_priority(self, index: int) -> MessagePriority:
        """获取消息优先级"""
        if index < 0:
            index = len(self._messages) + index
        return self._message_priorities.get(index, MessagePriority.NORMAL)
    
    def _auto_mark_important_messages(self) -> None:
        """自动检测并标记重要消息"""
        if not self.config.auto_mark_important:
            return
        
        important_indicators = [
            "重要", "关键", "必须", "不要忘记", "记住",
            "important", "critical", "must", "remember",
            "目标", "需求", "任务",
        ]
        
        for i, msg in enumerate(self._messages):
            if not msg.content:
                continue
            
            content_lower = msg.content.lower()
            
            # 用户的第一条消息（通常包含任务描述）
            if i == 0 and msg.role == "user":
                self.mark_important(i, MessagePriority.HIGH)
                continue
            
            # 包含重要关键词
            if any(ind in content_lower for ind in important_indicators):
                self.mark_important(i, MessagePriority.HIGH)
                continue
            
            # 包含明确的指令
            if msg.role == "user" and len(msg.content) > 50:
                # 较长的用户消息通常更重要
                self._message_priorities[i] = MessagePriority.HIGH
    
    # ==================== Lifecycle Hooks ====================
    
    def bootstrap(self, session_id: str, user_id: str = "") -> None:
        """
        会话开始时初始化上下文
        
        Args:
            session_id: 会话 ID
            user_id: 用户 ID
        """
        self._messages = []
        self._metadata = {
            "session_id": session_id,
            "user_id": user_id,
        }
        self._important_indices.clear()
        self._message_priorities.clear()
        logger.debug(f"ContextEngine bootstrap: session={session_id}")
    
    def ingest(self, message: "Message") -> None:
        """
        注入新消息到上下文
        
        Args:
            message: 要注入的消息
        """
        self._messages.append(message)
        
        # 自动标记重要消息
        if self.config.auto_mark_important:
            self._auto_mark_important_messages()
    
    @abstractmethod
    def assemble(self, system_prompt: str = "") -> list["Message"]:
        """
        组装最终发送给 LLM 的上下文
        
        这是核心方法，决定了哪些消息会被发送给 LLM。
        可以在这里实现消息过滤、排序、裁剪等逻辑。
        
        Args:
            system_prompt: 系统提示词
            
        Returns:
            组装后的消息列表
        """
        pass
    
    @abstractmethod
    async def compact(self, llm: "LLMRouter") -> bool:
        """
        压缩/裁剪上下文
        
        当上下文超出限制时调用。可以实现：
        - 简单截断（保留最近 N 条）
        - LLM 摘要压缩
        - 重要消息提取
        
        Args:
            llm: LLM Router，可用于生成摘要
            
        Returns:
            是否成功压缩
        """
        pass
    
    def after_turn(self, response: "Message") -> None:
        """
        对话轮次结束后的处理
        
        可以在这里实现：
        - 自动记忆提取
        - 上下文清理
        - 统计更新
        
        Args:
            response: LLM 的回复消息
        """
        pass
    
    def prepare_subagent(self, task: str) -> list["Message"]:
        """
        为子 Agent 准备上下文
        
        当需要启动子 Agent 处理子任务时调用。
        可以决定传递哪些上下文给子 Agent。
        
        Args:
            task: 子任务描述
            
        Returns:
            为子 Agent 准备的消息列表
        """
        return []
    
    # ==================== 辅助方法 ====================
    
    def count_tokens(self) -> int:
        """计算当前上下文的 token 数"""
        from .memory import count_messages_tokens
        return count_messages_tokens(self._messages)
    
    def needs_compaction(self) -> bool:
        """检查是否需要压缩"""
        token_threshold = int(self.config.max_tokens * self.config.compression_threshold)
        return (
            self.count_tokens() > token_threshold
            or len(self._messages) > self.config.max_messages
        )
    
    def clear(self) -> None:
        """清空上下文"""
        self._messages = []
        self._metadata = {}


class DefaultContextEngine(BaseContextEngine):
    """
    默认上下文管理引擎 v2.0
    
    升级特性：
    - 重要消息保护：标记为重要的消息不会被压缩
    - 滑动窗口压缩：分段压缩，保持上下文连贯性
    - 优先级感知：根据消息优先级决定压缩顺序
    
    实现了灵雀的标准上下文管理策略：
    - 基于 token 和消息数的双重限制
    - LLM 智能摘要压缩
    - tool_calls 消息配对验证
    """
    
    def assemble(self, system_prompt: str = "") -> list["Message"]:
        """组装上下文，确保消息配对正确"""
        from .memory import Memory
        
        # 验证消息配对
        validated = Memory._validate_message_pairs(self._messages)
        
        # 如果超出限制，截断（但保留重要消息）
        if len(validated) > self.config.max_messages:
            # 分离重要消息和普通消息
            important_msgs = []
            normal_msgs = []
            
            for i, msg in enumerate(validated):
                if self.is_important(i) or self.get_priority(i) == MessagePriority.CRITICAL:
                    important_msgs.append(msg)
                else:
                    normal_msgs.append(msg)
            
            # 计算可保留的普通消息数
            available_slots = self.config.max_messages - len(important_msgs)
            if available_slots > 0:
                # 保留最近的普通消息
                normal_msgs = normal_msgs[-available_slots:]
            else:
                normal_msgs = []
            
            # 合并（重要消息在前，普通消息在后，按原顺序）
            validated = important_msgs + normal_msgs
        
        return validated
    
    async def compact(self, llm: "LLMRouter") -> bool:
        """滑动窗口 + 重要消息保护的智能压缩"""
        if len(self._messages) < 10:
            return False
        
        try:
            # 分离重要消息和可压缩消息
            important_msgs = []
            compressible_msgs = []
            
            keep_count = self.config.keep_recent_messages
            recent_indices = set(range(len(self._messages) - keep_count, len(self._messages)))
            
            for i, msg in enumerate(self._messages):
                # 最近的消息不压缩
                if i in recent_indices:
                    continue
                
                # 重要消息单独保留
                if self.is_important(i) or self.get_priority(i) in (MessagePriority.CRITICAL, MessagePriority.HIGH):
                    important_msgs.append(msg)
                else:
                    compressible_msgs.append(msg)
            
            # 最近的消息
            recent_msgs = self._messages[-keep_count:] if keep_count else []
            
            if len(compressible_msgs) < 5:
                # 可压缩的消息太少，简单截断
                self._messages = important_msgs + recent_msgs
                return True
            
            # 滑动窗口压缩
            window_size = self.config.sliding_window_size
            summaries = []
            
            for i in range(0, len(compressible_msgs), window_size):
                window = compressible_msgs[i:i + window_size]
                if len(window) < 3:
                    # 窗口太小，直接保留
                    summaries.extend(window)
                    continue
                
                # 压缩这个窗口
                summary = await self._compress_window(llm, window, i // window_size + 1)
                if summary:
                    summaries.append(summary)
                else:
                    # 压缩失败，保留原消息
                    summaries.extend(window[-2:])  # 只保留最后 2 条
            
            # 重新组装消息
            from ..llm.base import Message
            self._messages = important_msgs + summaries + recent_msgs
            
            # 更新重要消息索引（因为顺序变了）
            new_important_indices = set(range(len(important_msgs)))
            self._important_indices = new_important_indices
            
            logger.info(f"滑动窗口压缩完成: {len(compressible_msgs)} 条 → {len(summaries)} 条")
            return True
            
        except Exception as e:
            logger.error(f"上下文压缩失败: {e}")
        
        # 压缩失败，降级为简单截断（保留重要消息）
        important = [m for i, m in enumerate(self._messages) if self.is_important(i)]
        recent = self._messages[-self.config.keep_recent_messages:]
        self._messages = important + recent
        return True
    
    async def _compress_window(self, llm: "LLMRouter", messages: list, window_num: int) -> "Message":
        """压缩一个窗口的消息"""
        try:
            summary_prompt = (
                f"请将以下对话片段 (窗口 #{window_num}) 压缩为 1-2 句话的摘要，"
                "保留关键操作和结果：\n\n"
            )
            for msg in messages:
                role_name = {"user": "用户", "assistant": "助手", "tool": "工具"}.get(msg.role, msg.role)
                content = msg.content[:200] if msg.content else "(无内容)"
                summary_prompt += f"[{role_name}] {content}\n"
            
            from ..llm.base import Message
            resp = await llm.chat(
                messages=[Message(role="user", content=summary_prompt)],
                tools=None,
                system_prompt="你是一个对话摘要助手，用 1-2 句话简洁准确地总结。",
            )
            
            if resp.content:
                return Message(
                    role="user",
                    content=f"[摘要 #{window_num}] {resp.content}"
                )
        except Exception as e:
            logger.warning(f"窗口压缩失败: {e}")
        
        return None
    
    def after_turn(self, response: "Message") -> None:
        """轮次结束后处理"""
        # 自动检测并标记重要消息
        self._auto_mark_important_messages()


class RAGContextEngine(BaseContextEngine):
    """
    RAG (Retrieval-Augmented Generation) 上下文引擎
    
    在组装上下文时自动检索相关记忆，注入到上下文中。
    适合需要大量历史知识的场景。
    """
    
    def __init__(self, config: Optional[ContextConfig] = None, top_k: int = 5):
        super().__init__(config)
        self.top_k = top_k  # 检索返回的最大结果数
    
    def assemble(self, system_prompt: str = "") -> list["Message"]:
        """组装上下文，包含 RAG 检索结果"""
        from .memory import Memory
        
        # 获取最近的用户消息作为查询
        query = ""
        for msg in reversed(self._messages):
            if msg.role == "user" and msg.content:
                query = msg.content
                break
        
        # RAG 检索
        rag_context = ""
        if query:
            try:
                from ..memory.vector_store import get_vector_memory
                memory = get_vector_memory()
                if memory and memory.count() > 0:
                    results = memory.search(query, top_k=self.top_k, min_score=0.3)
                    if results:
                        rag_parts = ["[相关历史记忆]"]
                        for item in results:
                            rag_parts.append(f"- {item.content[:200]}")
                        rag_context = "\n".join(rag_parts)
            except Exception as e:
                logger.debug(f"RAG 检索跳过: {e}")
        
        # 验证消息配对
        validated = Memory._validate_message_pairs(self._messages)
        
        # 如果有 RAG 结果，注入到第一条用户消息之前
        if rag_context and validated:
            from ..llm.base import Message
            rag_msg = Message(role="user", content=rag_context)
            # 插入到最近一条用户消息之前
            for i in range(len(validated) - 1, -1, -1):
                if validated[i].role == "user":
                    validated.insert(i, rag_msg)
                    break
        
        return validated
    
    async def compact(self, llm: "LLMRouter") -> bool:
        """RAG 模式下的压缩：保存重要信息到向量库"""
        try:
            from ..memory.vector_store import get_vector_memory
            memory = get_vector_memory()
            if memory is None:
                return False
            
            # 提取重要消息保存到向量库
            for msg in self._messages:
                if msg.role == "user" and msg.content and len(msg.content) > 20:
                    try:
                        memory.add(msg.content, metadata={"role": "user"})
                    except Exception:
                        pass
            
        except Exception as e:
            logger.debug(f"RAG 保存跳过: {e}")
        
        # 然后执行标准压缩
        self._messages = self._messages[-self.config.keep_recent_messages:]
        return True


# ==================== 工厂函数 ====================

_engine_registry: dict[str, type[BaseContextEngine]] = {
    "default": DefaultContextEngine,
    "rag": RAGContextEngine,
}


def register_context_engine(name: str, engine_class: type[BaseContextEngine]):
    """注册自定义上下文引擎"""
    _engine_registry[name] = engine_class
    logger.info(f"注册上下文引擎: {name}")


def create_context_engine(
    engine_type: str = "default",
    config: Optional[ContextConfig] = None,
    **kwargs
) -> BaseContextEngine:
    """
    创建上下文引擎实例
    
    Args:
        engine_type: 引擎类型 ("default", "rag", 或自定义注册的名称)
        config: 上下文配置
        **kwargs: 传递给引擎构造函数的额外参数
        
    Returns:
        上下文引擎实例
    """
    if engine_type not in _engine_registry:
        logger.warning(f"未知的上下文引擎类型: {engine_type}，使用 default")
        engine_type = "default"
    
    engine_class = _engine_registry[engine_type]
    return engine_class(config, **kwargs)


def list_context_engines() -> list[str]:
    """列出所有可用的上下文引擎"""
    return list(_engine_registry.keys())
