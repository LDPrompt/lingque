"""
🐦 记忆系统 (v5 - Token 精确计数 + 智能压缩)
- 短期记忆: 按 session_id 隔离的消息历史 (内存)
- 长期记忆: 跨会话持久化 (MEMORY.md + JSONL 日志)
- 消息保护:
  1. 运行时守卫: add_message 时确保 tool_calls 完整性
  2. 提交前修复: get_context_messages 时严格配对验证
- 智能压缩: 基于 token 阈值触发 LLM 摘要压缩 + 压缩前记忆刷写
- 会话管理: 空闲超时 / 每日重置 / 按天日志
"""

import contextvars
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

from ..llm.base import Message

logger = logging.getLogger("lobster.memory")


# ==================== 集中式敏感信息脱敏 ====================

_SECRET_PATTERNS = [
    # OpenAI / Anthropic keys
    re.compile(r'sk-ant-[a-zA-Z0-9\-_]{20,}'),
    # DeepSeek / OpenAI sk- keys (包含短横线的 UUID 格式如 sk-ce2ce4c5e6d54b10...)
    re.compile(r'sk-[a-zA-Z0-9\-]{20,}'),
    # GitHub tokens
    re.compile(r'ghp_[a-zA-Z0-9]{30,}'),
    re.compile(r'gho_[a-zA-Z0-9]{30,}'),
    # 飞书 App ID / cli_ 开头
    re.compile(r'cli_[a-zA-Z0-9]{12,}'),
    # 长十六进制串 (40位以上，避免误伤 MD5/git SHA；飞书 secret 由专用规则覆盖)
    re.compile(r'(?<![a-zA-Z0-9/])[0-9a-f]{40,}(?![a-zA-Z0-9])'),
    # Slack tokens
    re.compile(r'xoxb-[a-zA-Z0-9\-]{20,}'),
    re.compile(r'xoxp-[a-zA-Z0-9\-]{20,}'),
    # Bearer tokens
    re.compile(r'Bearer\s+[a-zA-Z0-9\-_.]{20,}'),
    # AWS access key
    re.compile(r'AKIA[0-9A-Z]{16}'),
    # JWT tokens
    re.compile(r'eyJ[a-zA-Z0-9\-_]{50,}\.[a-zA-Z0-9\-_]{50,}'),
    # Private keys
    re.compile(r'-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----'),
    # 飞书 open_id (ou_ 开头的长 hash)
    re.compile(r'ou_[a-zA-Z0-9]{20,}'),
    # Feishu / Lark app secret (32-char hex after field name)
    re.compile(r'(?i)(app[_-]?secret|encrypt[_-]?key|verification[_-]?token)["\']?\s*[:=]\s*["\']?([a-zA-Z0-9]{16,})'),
    # .env style: KEY_NAME=value (catches standalone env lines)
    re.compile(
        r'(?im)^(?:export\s+)?'
        r'(?:OPENAI_API_KEY|ANTHROPIC_API_KEY|DEEPSEEK_API_KEY|DOUBAO_API_KEY|'
        r'FEISHU_APP_SECRET|FEISHU_APP_ID|FEISHU_VERIFICATION_TOKEN|FEISHU_ENCRYPT_KEY|'
        r'ENCRYPT_KEY|VERIFICATION_TOKEN|GITHUB_TOKEN|AWS_SECRET_ACCESS_KEY|'
        r'DATABASE_PASSWORD|DB_PASSWORD|REDIS_PASSWORD|SECRET_KEY|PRIVATE_KEY)'
        r'\s*=\s*(.+)$',
    ),
    # key=value pattern with known field names
    re.compile(
        r'(?i)(api[_-]?key|api[_-]?secret|app[_-]?secret|secret[_-]?key|access[_-]?token|'
        r'auth[_-]?token|password|passwd|credential|private[_-]?key|encrypt[_-]?key|'
        r'verification[_-]?token|signing[_-]?secret|app[_-]?id)["\']?\s*[:=]\s*["\']?([a-zA-Z0-9\-_./+]{8,})',
    ),
]


def redact_secrets(text: str) -> str:
    """对文本中的 API Key、Token、密码等敏感信息做脱敏替换"""
    if not text:
        return text
    result = text
    for pat in _SECRET_PATTERNS:
        result = pat.sub("[REDACTED]", result)
    return result

# 异步安全的会话/用户指针，每个并发 Task 拥有独立值
_ctx_session: contextvars.ContextVar[str] = contextvars.ContextVar("memory_session", default="default")
_ctx_user: contextvars.ContextVar[str] = contextvars.ContextVar("memory_user", default="default")

# Token 计数器（tiktoken 可选，降级到字符估算）
_tokenizer = None
_tokenizer_loaded = False


def count_tokens(text: str) -> int:
    """精确计算 token 数，tiktoken 不可用时降级为字符估算"""
    global _tokenizer, _tokenizer_loaded
    if not _tokenizer_loaded:
        _tokenizer_loaded = True
        try:
            import tiktoken
            _tokenizer = tiktoken.get_encoding("cl100k_base")
            logger.info("tiktoken 已加载，使用精确 token 计数")
        except ImportError:
            logger.info("tiktoken 未安装，使用字符估算 (pip install tiktoken)")
    if _tokenizer:
        return len(_tokenizer.encode(text))
    return len(text) // 3 + 1


def _tc_tokens(tc) -> int:
    """计算单个 tool_call 的 token 数（兼容 object / dict 两种格式）"""
    if hasattr(tc, 'name'):
        return count_tokens(tc.name or "") + count_tokens(str(getattr(tc, 'arguments', '') or ""))
    if isinstance(tc, dict):
        fn = tc.get('function', tc)
        return count_tokens(str(fn.get('name', ''))) + count_tokens(str(fn.get('arguments', fn.get('input', ''))))
    return count_tokens(str(tc))


def count_messages_tokens(messages: list[Message]) -> int:
    """计算消息列表的总 token 数"""
    total = 0
    for msg in messages:
        total += 4  # role + formatting overhead
        if msg.content:
            total += count_tokens(msg.content)
        if msg.tool_calls:
            for tc in msg.tool_calls:
                total += _tc_tokens(tc)
    return total


class Memory:
    """记忆管理器（支持多会话隔离 + 智能压缩 + 会话持久化）"""

    def __init__(self, memory_dir: Path, max_context_messages: int = 50,
                 max_context_tokens: int = 32000,
                 idle_timeout_minutes: int = 120, daily_reset_hour: int = 4):
        self.memory_dir = Path(memory_dir).resolve()
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.max_context_messages = max_context_messages
        self.max_context_tokens = max_context_tokens
        self.idle_timeout_minutes = idle_timeout_minutes
        self.daily_reset_hour = daily_reset_hour

        # 多会话隔离（消息存储按 session_id 分桶，指针用 ContextVar 并发安全）
        self._sessions: dict[str, list[Message]] = {"default": []}
        self._pending_per_session: dict[str, set[str]] = {"default": set()}

        # 会话时间追踪
        self._session_start_times: dict[str, datetime] = {}
        self._session_last_active: dict[str, datetime] = {}

        # 待异步提取的会话消息（按 session_id 隔离，由 flush_session_memories 消费）
        self._pending_flush_messages: dict[str, list[Message]] = {}

        # 空闲超时后需要压缩的会话（由 set_session 标记，由 process_message 消费）
        self._needs_idle_compress: set[str] = set()

        # 会话持久化目录
        self._session_dir = self.memory_dir / "sessions"
        self._session_dir.mkdir(exist_ok=True)

        # 长期记忆文件
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.log_dir = self.memory_dir / "logs"
        self.log_dir.mkdir(exist_ok=True)

        if not self.memory_file.exists():
            self.memory_file.write_text(
                "# 🐦 Long-Term Memory\n\n"
                "> 这是 AI 助手的长期记忆文件，记录重要的用户偏好和关键信息\n\n"
                "## 用户偏好\n\n(尚无记录)\n\n"
                "## 重要信息\n\n(尚无记录)\n",
                encoding="utf-8",
            )

        # 启动时从磁盘恢复会话
        self._load_sessions_from_disk()

    # ------ 多会话管理 ------

    @property
    def _current_session(self) -> str:
        return _ctx_session.get()

    @property
    def _current_user_id(self) -> str:
        return _ctx_user.get()

    _MAX_SESSIONS = 50  # 最多保留的会话数量

    def set_session(self, session_id: str):
        """切换到指定会话（带空闲超时和每日重置检测）

        空闲超时 → 标记压缩（保留上下文摘要，用户回来能接着聊）
        每日重置 → 清空（每天一个干净起点）
        """
        _ctx_session.set(session_id)
        now = datetime.now()

        if session_id in self._sessions:
            idle_timeout = False
            daily_reset = False

            # 空闲超时检测
            last_active = self._session_last_active.get(session_id)
            if last_active and (now - last_active) > timedelta(minutes=self.idle_timeout_minutes):
                idle_timeout = True

            # 每日重置检测（跨过了当天的重置时刻）
            start_time = self._session_start_times.get(session_id)
            if start_time:
                today_reset = now.replace(hour=self.daily_reset_hour, minute=0, second=0, microsecond=0)
                if start_time < today_reset <= now:
                    daily_reset = True

            if daily_reset:
                # 每日重置：完全清空，提取记忆
                logger.info(f"会话 {session_id} 跨日重置")
                old_msgs = list(self._sessions.get(session_id, []))
                self._save_session_summary(session_id)
                if len(old_msgs) >= 6:
                    self._pending_flush_messages[session_id] = old_msgs
                self._sessions[session_id] = []
                self._pending_per_session[session_id] = set()
                self._session_start_times[session_id] = now
                self._delete_session_file(session_id)
            elif idle_timeout and len(self._sessions.get(session_id, [])) >= 4:
                # 空闲超时：标记压缩（不清空，保留上下文），下次 process_message 时压缩
                logger.info(f"会话 {session_id} 空闲超时 ({self.idle_timeout_minutes}min)，标记待压缩")
                self._needs_idle_compress.add(session_id)

        if session_id not in self._sessions:
            self._sessions[session_id] = []
            self._session_start_times[session_id] = now
        if session_id not in self._pending_per_session:
            self._pending_per_session[session_id] = set()

        self._session_last_active[session_id] = now

        # 定期清理过期会话，防止字典无限增长
        if len(self._sessions) > self._MAX_SESSIONS:
            self._gc_stale_sessions(now)

    def _gc_stale_sessions(self, now: datetime):
        """清理超过 24 小时不活跃的会话，防止内存泄漏"""
        stale_ids = []
        for sid, last_active in self._session_last_active.items():
            if sid == "default":
                continue
            idle_hours = (now - last_active).total_seconds() / 3600
            if idle_hours > 24:
                stale_ids.append(sid)

        # 如果没有过期的但总数仍超限，淘汰最老的
        if not stale_ids and len(self._sessions) > self._MAX_SESSIONS:
            sorted_sessions = sorted(
                self._session_last_active.items(),
                key=lambda x: x[1]
            )
            # 淘汰最不活跃的，保留一半
            for sid, _ in sorted_sessions[:len(sorted_sessions) // 2]:
                if sid != "default":
                    stale_ids.append(sid)

        for sid in stale_ids:
            self._sessions.pop(sid, None)
            self._pending_per_session.pop(sid, None)
            self._session_start_times.pop(sid, None)
            self._session_last_active.pop(sid, None)
            self._pending_flush_messages.pop(sid, None)

        if stale_ids:
            logger.info(f"清理了 {len(stale_ids)} 个过期会话，剩余 {len(self._sessions)} 个")
            for sid in stale_ids:
                self._delete_session_file(sid)

    # ------ 会话持久化 ------

    @staticmethod
    def _session_id_to_filename(session_id: str) -> str:
        """把 session_id 转为安全文件名（替换冒号等特殊字符）"""
        import re as _re
        return _re.sub(r'[^a-zA-Z0-9_\-]', '_', session_id) + ".json"

    def _message_to_dict(self, msg: Message) -> dict:
        """Message 序列化为 dict（跳过 base64 图片和内部标记）"""
        d: dict = {"role": msg.role, "content": msg.content or ""}
        if msg.name:
            d["name"] = msg.name
        if msg.tool_call_id:
            d["tool_call_id"] = msg.tool_call_id
        if msg.tool_calls:
            d["tool_calls"] = [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in msg.tool_calls
            ]
        if msg.reasoning_content:
            d["reasoning_content"] = msg.reasoning_content
        return d

    @staticmethod
    def _dict_to_message(d: dict) -> Message:
        """dict 反序列化为 Message"""
        from ..llm.base import ToolCall as TC
        tool_calls = []
        for tc_data in d.get("tool_calls", []):
            tool_calls.append(TC(
                id=tc_data.get("id", ""),
                name=tc_data.get("name", ""),
                arguments=tc_data.get("arguments", {}),
            ))
        return Message(
            role=d.get("role", "user"),
            content=d.get("content", ""),
            name=d.get("name", ""),
            tool_call_id=d.get("tool_call_id", ""),
            tool_calls=tool_calls,
            reasoning_content=d.get("reasoning_content", ""),
        )

    def _persist_session(self, session_id: str):
        """将指定会话保存到磁盘"""
        msgs = self._sessions.get(session_id, [])
        filepath = self._session_dir / self._session_id_to_filename(session_id)

        if not msgs:
            filepath.unlink(missing_ok=True)
            return

        data = {
            "session_id": session_id,
            "start_time": self._session_start_times.get(session_id, datetime.now()).isoformat(),
            "last_active": self._session_last_active.get(session_id, datetime.now()).isoformat(),
            "messages": [self._message_to_dict(m) for m in msgs],
        }
        try:
            tmp = filepath.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=None), encoding="utf-8")
            tmp.replace(filepath)
        except Exception as e:
            logger.warning(f"持久化会话 {session_id} 失败: {e}")

    def _delete_session_file(self, session_id: str):
        """删除指定会话的磁盘文件"""
        filepath = self._session_dir / self._session_id_to_filename(session_id)
        filepath.unlink(missing_ok=True)

    def _load_sessions_from_disk(self):
        """启动时从磁盘恢复所有会话"""
        if not self._session_dir.exists():
            return

        loaded = 0
        now = datetime.now()
        for filepath in self._session_dir.glob("*.json"):
            try:
                data = json.loads(filepath.read_text(encoding="utf-8"))
                session_id = data.get("session_id", "")
                if not session_id:
                    continue

                # 跳过过期会话（超过 24 小时不活跃的不恢复）
                last_active_str = data.get("last_active", "")
                if last_active_str:
                    try:
                        last_active = datetime.fromisoformat(last_active_str)
                        if (now - last_active).total_seconds() > 86400:
                            filepath.unlink(missing_ok=True)
                            continue
                    except (ValueError, TypeError):
                        pass

                messages = [self._dict_to_message(d) for d in data.get("messages", [])]
                if not messages:
                    filepath.unlink(missing_ok=True)
                    continue

                self._sessions[session_id] = messages

                start_str = data.get("start_time", "")
                try:
                    self._session_start_times[session_id] = datetime.fromisoformat(start_str) if start_str else now
                except (ValueError, TypeError):
                    self._session_start_times[session_id] = now

                try:
                    self._session_last_active[session_id] = datetime.fromisoformat(last_active_str) if last_active_str else now
                except (ValueError, TypeError):
                    self._session_last_active[session_id] = now

                self._pending_per_session[session_id] = set()
                loaded += 1

            except Exception as e:
                logger.warning(f"恢复会话文件 {filepath.name} 失败: {e}")

        if loaded:
            logger.info(f"📂 从磁盘恢复了 {loaded} 个会话")

    @staticmethod
    def _sanitize_user_id(user_id: str) -> str:
        """清理 user_id，防止路径穿越（仅保留字母、数字、下划线、连字符）"""
        import re
        safe = re.sub(r'[^a-zA-Z0-9_\-]', '_', user_id)
        if not safe or safe in ('.', '..'):
            safe = 'default'
        return safe

    def set_user(self, user_id: str):
        """设置当前用户 ID，用于隔离长期记忆和日志"""
        _ctx_user.set(self._sanitize_user_id(user_id) if user_id else "default")

    def _get_user_memory_dir(self) -> Path:
        user_dir = self.memory_dir / "users" / self._current_user_id
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir

    def _get_user_memory_file(self) -> Path:
        user_dir = self._get_user_memory_dir()
        memory_file = user_dir / "MEMORY.md"
        if not memory_file.exists():
            memory_file.write_text(
                f"# 🐦 {self._current_user_id} 的记忆\n\n"
                "> 这是该用户的专属长期记忆\n\n"
                "## 用户偏好\n\n(尚无记录)\n\n"
                "## 重要信息\n\n(尚无记录)\n",
                encoding="utf-8",
            )
        return memory_file

    def _get_user_log_dir(self) -> Path:
        log_dir = self._get_user_memory_dir() / "logs"
        log_dir.mkdir(exist_ok=True)
        return log_dir

    @property
    def messages(self) -> list[Message]:
        if self._current_session not in self._sessions:
            self._sessions[self._current_session] = []
        return self._sessions[self._current_session]

    @messages.setter
    def messages(self, value):
        self._sessions[self._current_session] = value

    @property
    def _pending_tool_call_ids(self) -> set[str]:
        if self._current_session not in self._pending_per_session:
            self._pending_per_session[self._current_session] = set()
        return self._pending_per_session[self._current_session]

    @_pending_tool_call_ids.setter
    def _pending_tool_call_ids(self, value):
        self._pending_per_session[self._current_session] = value

    # ------ 运行时守卫 ------

    def add_message(self, message: Message):
        """添加消息到当前会话（带运行时守卫）"""
        if message.role != "tool" and self._pending_tool_call_ids:
            missing_ids = list(self._pending_tool_call_ids)
            logger.warning(f"运行时守卫 [{self._current_session}]: "
                           f"补全 {len(missing_ids)} 个未响应的 tool_call")
            for tc_id in missing_ids:
                synthetic = Message(
                    role="tool",
                    content="[工具响应丢失 - 已自动补全]",
                    tool_call_id=tc_id,
                    name="_synthetic",
                )
                self.messages.append(synthetic)
                self._log_message(synthetic)
            self._pending_tool_call_ids.clear()

        if message.role == "assistant" and message.tool_calls:
            self._pending_tool_call_ids = {tc.id for tc in message.tool_calls}

        if message.role == "tool" and message.tool_call_id:
            self._pending_tool_call_ids.discard(message.tool_call_id)

        self.messages.append(message)
        self._log_message(message)

        # 更新活跃时间
        self._session_last_active[self._current_session] = datetime.now()

        # 持久化到磁盘（仅在用户消息或助手最终回复时保存，减少 IO）
        if message.role in ("user", "assistant") and not message.tool_calls:
            self._persist_session(self._current_session)

    # ------ 上下文获取（含配对验证）------

    MAX_CONTEXT_CHARS = 50000
    MAX_SINGLE_MSG_CHARS = 4000

    def get_context_messages(self) -> list[Message]:
        """获取用于发送给 LLM 的消息列表（严格配对验证 + 安全截断兜底）"""
        # 补全悬挂的 tool_calls
        if self._pending_tool_call_ids:
            logger.warning(f"提交前修复: 补全 {len(self._pending_tool_call_ids)} 个悬挂的 tool_call")
            for tc_id in list(self._pending_tool_call_ids):
                self.messages.append(Message(
                    role="tool",
                    content="[工具执行超时或异常]",
                    tool_call_id=tc_id,
                    name="_synthetic",
                ))
            self._pending_tool_call_ids.clear()

        validated = self._validate_message_pairs(self.messages)

        if len(validated) != len(self.messages):
            logger.warning(f"配对验证: {len(self.messages)} -> {len(validated)} 条消息")
            self.messages = list(validated)

        # 单条消息截断保护（深拷贝 tool_calls 避免破坏原始数据）
        import copy
        validated = [copy.copy(m) for m in validated]
        for msg in validated:
            if msg.content and len(msg.content) > self.MAX_SINGLE_MSG_CHARS:
                original_len = len(msg.content)
                msg.content = msg.content[:self.MAX_SINGLE_MSG_CHARS] + \
                    f"\n... [内容已截断，原始 {original_len} 字符]"
            if msg.tool_calls:
                new_tcs = []
                for tc in msg.tool_calls:
                    if isinstance(getattr(tc, 'arguments', None), dict):
                        args_str = str(tc.arguments)
                        if len(args_str) > self.MAX_SINGLE_MSG_CHARS:
                            tc = copy.copy(tc)
                            tc.arguments = {"_truncated": args_str[:self.MAX_SINGLE_MSG_CHARS // 2]}
                    new_tcs.append(tc)
                msg.tool_calls = new_tcs

        # 安全截断兜底（配对感知：使用 _find_safe_split 寻找安全分割点）
        if len(validated) <= self.max_context_messages:
            result = list(validated)
        else:
            keep_n = self.max_context_messages - 1  # 预留 1 条给 omitted 提示
            split_idx = self._find_safe_split(validated, keep_n)
            if split_idx <= 0:
                result = list(validated[-self.max_context_messages:])
            else:
                tail = validated[split_idx:]
                result = [Message(role="system", content="[...earlier messages omitted...]")] + tail

        def _msg_chars(m):
            chars = len(m.content or "")
            if hasattr(m, 'tool_calls') and m.tool_calls:
                for tc in m.tool_calls:
                    if hasattr(tc, 'name'):
                        chars += len(tc.name or "") + len(str(getattr(tc, 'arguments', '') or ""))
                    elif isinstance(tc, dict):
                        fn = tc.get('function', tc)
                        chars += len(str(fn.get('name', ''))) + len(str(fn.get('arguments', fn.get('input', ''))))
                    else:
                        chars += len(str(tc))
            return chars

        total_chars = sum(_msg_chars(m) for m in result)
        while total_chars > self.MAX_CONTEXT_CHARS and len(result) > 4:
            removed = result.pop(1)
            total_chars -= _msg_chars(removed)
            # 如果刚移除了 assistant(tool_calls)，连带移除其 tool 响应
            while (len(result) > 1 and result[1].role == "tool"):
                removed = result.pop(1)
                total_chars -= _msg_chars(removed)

        # 最终兜底验证：确保发给 LLM 的消息绝对配对
        result = self._validate_message_pairs(result)

        return result

    @staticmethod
    def _validate_message_pairs(messages: list[Message]) -> list[Message]:
        """确保 tool_calls 和 tool result 严格配对（单次前向扫描）"""
        if not messages:
            return []

        validated = []
        i = 0
        while i < len(messages):
            msg = messages[i]

            if msg.role == "assistant" and msg.tool_calls:
                expected_ids = {tc.id for tc in msg.tool_calls}
                found_ids = set()
                j = i + 1
                tool_results = []

                while j < len(messages) and messages[j].role == "tool":
                    found_ids.add(messages[j].tool_call_id)
                    tool_results.append(messages[j])
                    j += 1

                if expected_ids == found_ids:
                    validated.append(msg)
                    validated.extend(tool_results)
                else:
                    logger.warning(f"配对验证: 跳过不完整的 tool_calls "
                                   f"(期望 {len(expected_ids)}, 实际 {len(found_ids)})")
                i = j

            elif msg.role == "tool":
                # 孤儿 tool 消息（没有前置 assistant），跳过
                i += 1

            else:
                validated.append(msg)
                i += 1

        # 移除尾部不完整的 assistant+tool_calls
        while validated and validated[-1].role == "assistant" and validated[-1].tool_calls:
            validated.pop()

        return validated

    def _save_session_summary(self, session_id: str):
        """会话重置前自动保存任务摘要到每日日志，防止"失忆" """
        msgs = self._sessions.get(session_id, [])
        if len(msgs) < 4:
            return

        tasks = []
        for msg in msgs:
            if msg.role == "user" and msg.content and not msg.content.startswith("[系统"):
                text = msg.content[:100].replace("\n", " ")
                tasks.append(f"- 用户: {text}")
            elif msg.role == "assistant" and msg.content and not msg.tool_calls:
                text = msg.content[:100].replace("\n", " ")
                tasks.append(f"- 回复: {text}")

        if not tasks:
            return

        # 只保留前 20 条，避免日志过大
        if len(tasks) > 20:
            tasks = tasks[:20] + [f"- ... 共 {len(tasks)} 条交互"]

        summary = f"**会话 {session_id} 摘要**\n" + "\n".join(tasks)
        self.save_daily_note(summary)
        logger.info(f"会话 {session_id} 重置前已保存摘要 ({len(tasks)} 条)")

    async def flush_session_memories(self, llm_router) -> None:
        """会话重置后，用 LLM 智能提取旧对话中的记忆写入长期记忆"""
        session_id = self._current_session
        msgs = self._pending_flush_messages.pop(session_id, [])
        if not msgs:
            return

        content = "\n".join(
            f"{m.role}: {m.content[:200]}"
            for m in msgs if m.content and m.role != "tool"
        )
        if not content.strip():
            return
        content = redact_secrets(content)

        prompt = (
            "以下是一段已结束的对话记录。请完成三件事：\n"
            "1. 提取值得长期记住的信息（用户偏好、重要决策、项目信息、关键事实）\n"
            "2. 用一句话总结用户让你做了什么任务\n"
            "3. 提取经验教训（什么做法有效、什么应该避免、遇到了什么坑）\n\n"
            "**绝对禁止记录任何密码、token、secret、API key 等敏感凭证。**\n"
            "如果没有值得记住的，回复 NO_FLUSH。\n"
            "如果有，用以下格式输出：\n"
            "MEMORY: (需要长期记住的内容)\n"
            "TASK: (一句话任务摘要)\n"
            "LESSON: (经验教训，没有则省略此行)\n\n"
            f"{content}"
        )

        try:
            response = await llm_router.chat(
                messages=[Message(role="user", content=prompt)],
                system_prompt="你是记忆管理助手，只输出需要记录的内容。",
                temperature=0.3,
            )
            if response.content and "NO_FLUSH" not in response.content:
                text = response.content
                memory_part = ""
                task_part = ""
                lesson_part = ""

                if "LESSON:" in text:
                    text, lesson_part = text.split("LESSON:", 1)
                    lesson_part = lesson_part.strip()[:300]

                if "MEMORY:" in text:
                    parts = text.split("MEMORY:", 1)[1]
                    if "TASK:" in parts:
                        memory_part = parts.split("TASK:", 1)[0].strip()
                        task_part = parts.split("TASK:", 1)[1].strip()
                    else:
                        memory_part = parts.strip()
                elif "TASK:" in text:
                    task_part = text.split("TASK:", 1)[1].strip()
                else:
                    memory_part = text.strip()

                if memory_part:
                    self.save_to_long_term("自动记忆提取", memory_part)
                    logger.info(f"会话重置记忆提取成功: {memory_part[:80]}...")

                if task_part:
                    self.save_daily_note(f"**任务**: {task_part}")

                if lesson_part:
                    try:
                        from ..memory.learning_engine import get_learning_engine
                        le = get_learning_engine()
                        if le:
                            le.record({
                                "type": "learning",
                                "category": "session_lesson",
                                "context": task_part[:100] if task_part else "会话重置提取",
                                "learning": lesson_part,
                            })
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"会话重置记忆提取失败: {e}")

    def clear_session(self):
        """清空当前会话"""
        self._save_session_summary(self._current_session)
        self.messages.clear()
        self._pending_tool_call_ids.clear()
        self._delete_session_file(self._current_session)

    # ------ 智能压缩 ------

    async def flush_before_compaction(self, llm_router) -> None:
        """压缩前让 LLM 提取值得长期记住的信息 + 写入每日日志"""
        if len(self.messages) < 20:
            return

        recent = self.messages[-20:]
        content = "\n".join(
            f"{m.role}: {m.content[:200]}"
            for m in recent if m.content and m.role != "tool"
        )
        if not content.strip():
            return
        content = redact_secrets(content)

        prompt = (
            "以下是最近的对话记录。请完成三件事：\n"
            "1. 提取值得长期记住的信息（用户偏好、重要决策、项目信息、关键事实）\n"
            "2. 用一句话总结用户让你做了什么任务\n"
            "3. 提取经验教训（什么做法有效、什么应该避免、遇到了什么坑）\n\n"
            "**绝对禁止记录任何密码、token、secret、API key 等敏感凭证。**\n"
            "如果没有值得记住的，回复 NO_FLUSH。\n"
            "如果有，用以下格式输出：\n"
            "MEMORY: (需要长期记住的内容)\n"
            "TASK: (一句话任务摘要)\n"
            "LESSON: (经验教训，没有则省略此行)\n\n"
            f"{content}"
        )

        try:
            response = await llm_router.chat(
                messages=[Message(role="user", content=prompt)],
                system_prompt="你是记忆管理助手，只输出需要记录的内容。",
                temperature=0.3,
            )
            if response.content and "NO_FLUSH" not in response.content:
                text = response.content

                memory_part = ""
                task_part = ""
                lesson_part = ""

                # 提取 LESSON 部分（先提取再处理其余）
                if "LESSON:" in text:
                    text, lesson_part = text.split("LESSON:", 1)
                    lesson_part = lesson_part.strip()[:300]

                if "MEMORY:" in text:
                    parts = text.split("MEMORY:", 1)[1]
                    if "TASK:" in parts:
                        memory_part = parts.split("TASK:", 1)[0].strip()
                        task_part = parts.split("TASK:", 1)[1].strip()
                    else:
                        memory_part = parts.strip()
                elif "TASK:" in text:
                    task_part = text.split("TASK:", 1)[1].strip()
                else:
                    memory_part = text.strip()

                if memory_part:
                    self.save_to_long_term("自动记忆提取", memory_part)

                if task_part:
                    self.save_daily_note(f"**任务**: {task_part}")
                elif memory_part:
                    self.save_daily_note(f"**记忆提取**: {memory_part[:200]}")

                # 经验教训 → 写入 LearningEngine
                if lesson_part:
                    try:
                        from ..memory.learning_engine import get_learning_engine
                        le = get_learning_engine()
                        if le:
                            le.record({
                                "type": "learning",
                                "category": "compaction_lesson",
                                "context": task_part[:100] if task_part else "上下文压缩提取",
                                "learning": lesson_part,
                            })
                    except Exception:
                        pass

                logger.info("压缩前记忆刷写完成")
        except Exception as e:
            logger.error(f"记忆刷写失败: {e}")

    @staticmethod
    def _find_safe_split(messages: list[Message], raw_split: int) -> int:
        """找到安全的分割点，确保不会把 tool_calls 和 tool 响应拆开。
        返回 keep 区起始索引（即 to_keep = messages[idx:]）。"""
        n = len(messages)
        idx = n - raw_split
        if idx <= 0:
            return 0
        if idx >= n:
            return n

        # 向前调整：如果 idx 落在 tool 消息上（属于前面的 assistant），
        # 或落在 assistant(tool_calls) 的 tool 响应区间内，
        # 就往前移到 assistant(tool_calls) 的位置。
        while idx > 0:
            if messages[idx].role == "tool":
                idx -= 1
                continue
            if (idx > 0 and messages[idx - 1].role == "assistant"
                    and messages[idx - 1].tool_calls):
                idx -= 1
                continue
            break

        # 向后调整兜底：如果 idx 指向 assistant(tool_calls)，
        # 确认其所有 tool 响应都在 keep 区内
        if (idx < n and messages[idx].role == "assistant"
                and messages[idx].tool_calls):
            expected = {tc.id for tc in messages[idx].tool_calls}
            j = idx + 1
            found = set()
            while j < n and messages[j].role == "tool":
                found.add(messages[j].tool_call_id)
                j += 1
            if expected != found:
                idx = j  # 配对不完整，整组跳过

        return idx

    async def compress_context(self, llm_router, target_count: int = 20) -> None:
        """用 LLM 总结压缩上下文（基于 token 阈值动态决定保留量）"""
        if len(self.messages) <= target_count:
            return

        await self.flush_before_compaction(llm_router)

        # 基于 token 预算动态决定保留多少消息（含 tool_calls 开销）
        target_tokens = self.max_context_tokens // 2
        keep_count = 0
        keep_tokens = 0
        for msg in reversed(self.messages):
            msg_tokens = count_tokens(msg.content or "")
            if hasattr(msg, 'tool_calls') and msg.tool_calls:
                for tc in msg.tool_calls:
                    msg_tokens += _tc_tokens(tc)
            if keep_tokens + msg_tokens > target_tokens and keep_count >= 6:
                break
            keep_tokens += msg_tokens
            keep_count += 1
        # P1 修复: 用 min 让 token 预算作为上限，避免压缩后仍超标
        keep_count = max(6, min(keep_count, target_count, len(self.messages)))

        split_idx = self._find_safe_split(self.messages, keep_count)
        to_compress = self.messages[:split_idx]
        to_keep = self.messages[split_idx:]

        # 保护干预消息：从待压缩部分提取并追加到保留部分最前面
        intervention_msgs = [
            m for m in to_compress
            if getattr(m, '_is_intervention', False)
        ]
        to_compress = [
            m for m in to_compress
            if not getattr(m, '_is_intervention', False)
        ]

        content = "\n".join(
            f"[{m.role}] {m.content[:300]}"
            for m in to_compress if m.content and m.role != "tool"
        )

        if not content.strip():
            self.messages = to_keep
            return
        content = redact_secrets(content)

        try:
            response = await llm_router.chat(
                messages=[Message(role="user", content=(
                    "请将以下对话历史压缩为一段简洁的摘要，保留关键信息和上下文。\n"
                    "用中文输出，控制在 300 字以内。\n\n"
                    f"{content}"
                ))],
                system_prompt="你是对话摘要助手。只输出摘要，不要加任何前缀。",
                temperature=0.3,
            )
            summary_msg = Message(
                role="system",
                content=f"[历史摘要] {response.content}",
            )
            self.messages = [summary_msg] + intervention_msgs + to_keep
            logger.info(f"上下文压缩完成: {len(to_compress) + len(to_keep)} -> {len(self.messages)} 条消息")
        except Exception:
            self.messages = intervention_msgs + to_keep
            logger.warning("LLM 摘要失败，退回简单截断")
        self._persist_session(self._current_session)

    # ------ 长期记忆 ------

    def load_long_term(self) -> str:
        """加载长期记忆：全局记忆 + 用户专属记忆"""
        global_memory = ""
        if self.memory_file.exists():
            global_memory = self.memory_file.read_text(encoding="utf-8")

        user_memory = ""
        if self._current_user_id != "default":
            user_file = self._get_user_memory_file()
            if user_file.exists():
                user_memory = user_file.read_text(encoding="utf-8")

        parts = []
        if global_memory:
            parts.append(f"## 全局记忆\n{global_memory}")
        if user_memory:
            parts.append(f"## {self._current_user_id} 的专属记忆\n{user_memory}")

        result = "\n\n---\n\n".join(parts) if parts else ""
        return redact_secrets(result) if result else ""

    def save_to_long_term(self, section: str, content: str):
        """保存到当前用户的长期记忆（自动脱敏）"""
        content = redact_secrets(content)
        if self._current_user_id != "default":
            target_file = self._get_user_memory_file()
        else:
            target_file = self.memory_file

        current = target_file.read_text(encoding="utf-8")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"\n### [{timestamp}] {section}\n{content}\n"
        target_file.write_text(current + entry, encoding="utf-8")
        logger.info(f"已保存长期记忆 (用户={self._current_user_id}): {section}")

    # ------ 每日日志 ------

    def save_daily_note(self, content: str):
        """追加内容到当前用户今天的 Markdown 日志（自动脱敏）"""
        content = redact_secrets(content)
        log_dir = self._get_user_log_dir() if self._current_user_id != "default" else self.log_dir
        today = datetime.now().strftime("%Y-%m-%d")
        path = log_dir / f"{today}.md"
        timestamp = datetime.now().strftime("%H:%M")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n### {timestamp}\n{content}\n")

    def load_recent_daily_notes(self, days: int = 2) -> str:
        """加载当前用户最近几天的 Markdown 日志"""
        log_dir = self._get_user_log_dir() if self._current_user_id != "default" else self.log_dir
        notes = []
        for i in range(days):
            date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            path = log_dir / f"{date}.md"
            if path.exists():
                content = path.read_text(encoding="utf-8")
                if content.strip():
                    notes.append(f"## {date}\n{content}")
        return "\n\n".join(notes) if notes else ""

    # ------ JSONL 日志 ------

    @staticmethod
    def _redact_sensitive(text: str) -> str:
        """脱敏日志中的密码、token 等敏感信息"""
        return redact_secrets(text)

    def _log_message(self, message: Message):
        log_dir = self._get_user_log_dir() if self._current_user_id != "default" else self.log_dir
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = log_dir / f"{today}.jsonl"
        record = {
            "timestamp": datetime.now().isoformat(),
            "session": self._current_session,
            "user_id": self._current_user_id,
            "role": message.role,
            "content": self._redact_sensitive(message.content[:500]) if message.content else "",
            "name": message.name,
        }
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ------ 统计 ------

    def get_stats(self) -> dict:
        total_messages = sum(len(msgs) for msgs in self._sessions.values())
        return {
            "session_messages": len(self.messages),
            "total_sessions": len(self._sessions),
            "total_messages": total_messages,
            "current_session": self._current_session,
            "memory_file_size": self.memory_file.stat().st_size if self.memory_file.exists() else 0,
            "log_files": len(list(self.log_dir.glob("*.jsonl"))),
        }
