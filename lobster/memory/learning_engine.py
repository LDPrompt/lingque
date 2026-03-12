"""
灵雀 - 自我进化引擎 (LearningEngine)

统一管理所有学习数据的存储和检索:
- JSONL 持久化（轻量、可审计）
- 可切换检索后端: SQLite FTS5 (轻量) / ChromaDB 向量 (语义)
- 工具统计（成功率 / 耗时）

通过环境变量 LEARNING_BACKEND 切换后端:
  sqlite (默认) — 零依赖, <5MB 内存, 适合 2C/4G 小服务器
  vector — 语义搜索, 需要 500MB+, 适合大服务器
"""

import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("lingque.learning")

_engine: Optional["LearningEngine"] = None


class LearningEngine:
    FEEDBACK_POSITIVE = re.compile(
        r"^(很好|对了|完美|不错|厉害|可以|感谢|谢谢|正确|没错|棒|好的|对的|太好了|非常好)",
    )
    FEEDBACK_NEGATIVE = re.compile(
        r"^(不对|错了|不是|重来|别这样|不行|不要|搞错|有问题|有bug|不好用|不太对)",
    )

    def __init__(self, workspace_dir: str | Path):
        self._workspace = Path(workspace_dir).resolve()
        self._learnings_dir = self._workspace / ".learnings"
        self._learnings_dir.mkdir(parents=True, exist_ok=True)

        self._jsonl_path = self._learnings_dir / "LEARNINGS.jsonl"
        self._stats_path = self._learnings_dir / "tool_stats.json"

        self._tool_stats: dict = self._load_tool_stats()

        from .search_backend import create_backend
        self._backend_type = os.environ.get("LEARNING_BACKEND", "sqlite")
        self._backend = create_backend(
            self._backend_type,
            db_path=self._learnings_dir / "learnings.db",
        )

    # ==================== JSONL + 向量双写 ====================

    def record(self, entry: dict) -> None:
        """写入一条学习记录到 JSONL + 向量库"""
        entry.setdefault("timestamp", datetime.now().isoformat())
        try:
            with open(self._jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug(f"JSONL 写入失败: {e}")

        self._add_to_vector(entry)

    def _add_to_vector(self, entry: dict) -> None:
        """将学习记录写入检索后端"""
        try:
            content = self._entry_to_text(entry)
            if not content or len(content) < 10:
                return

            meta = {
                "type": entry.get("type", "unknown"),
                "category": entry.get("category", "general"),
                "timestamp": entry.get("timestamp", ""),
                "source": "learning_engine",
            }
            self._backend.add(content, meta)
        except Exception as e:
            logger.debug(f"检索后端写入失败: {e}")

    @staticmethod
    def _entry_to_text(entry: dict) -> str:
        """将学习记录转为适合语义检索的文本"""
        t = entry.get("type", "")
        parts = []

        if t == "error_fix":
            parts.append(f"错误: {entry.get('error_msg', '')}")
            parts.append(f"工具: {entry.get('error_tool', '')}")
            fix = entry.get("fix_tool", "")
            if fix:
                parts.append(f"修复: 用 {fix} 解决")
        elif t == "reflection":
            parts.append(f"经验: {entry.get('lesson', '')}")
            tools = entry.get("tools_used", [])
            if tools:
                parts.append(f"涉及工具: {', '.join(tools[:5])}")
        elif t == "user_feedback":
            sentiment = entry.get("sentiment", "")
            parts.append(f"用户反馈: {'好评' if sentiment == 'positive' else '差评'}")
            parts.append(entry.get("context", ""))
        elif t in ("learning", "error", "auto_error"):
            ctx = entry.get("context", "")
            learning = entry.get("learning", "")
            err = entry.get("error_message", "")
            fix = entry.get("fix", "")
            if ctx:
                parts.append(ctx)
            if learning:
                parts.append(f"经验: {learning}")
            if err:
                parts.append(f"错误: {err}")
            if fix:
                parts.append(f"修复: {fix}")
        else:
            parts.append(entry.get("context", "") or entry.get("content", ""))

        return " | ".join(p for p in parts if p)[:500]

    # ==================== 模块 1: 任务反思 ====================

    def record_reflection(
        self,
        task_summary: str,
        tools_used: list[str],
        error_count: int,
        lesson: str = "",
    ) -> None:
        entry = {
            "type": "reflection",
            "task_summary": task_summary[:200],
            "tools_used": tools_used[:10],
            "error_count": error_count,
            "lesson": lesson[:300],
        }
        self.record(entry)
        logger.info(f"任务反思: errors={error_count}, tools={len(tools_used)}")

    # ==================== 模块 2: 错误-修复配对 ====================

    def record_error_fix(
        self,
        error_tool: str,
        error_msg: str,
        fix_tool: str,
        fix_args_summary: str = "",
    ) -> None:
        entry = {
            "type": "error_fix",
            "category": "tool_error",
            "error_tool": error_tool,
            "error_msg": error_msg[:300],
            "fix_tool": fix_tool,
            "fix_args_summary": fix_args_summary[:200],
        }
        self.record(entry)
        logger.info(f"错误修复配对: {error_tool} → {fix_tool}")

    def recall_fix_for_error(self, error_msg: str) -> Optional[str]:
        """搜索检索后端，看是否有类似错误的修复方案"""
        try:
            if self._backend.count() == 0:
                return None
            results = self._backend.search(
                f"错误: {error_msg[:200]}",
                top_k=2,
                filter_type="error_fix",
                min_score=0.3,
            )
            if not results:
                return None
            best = results[0]
            return f"[经验召回] 之前类似错误的处理方式: {best.content[:300]}"
        except Exception:
            return None

    # ==================== 模块 3: 用户反馈捕获 ====================

    def detect_and_record_feedback(
        self,
        user_message: str,
        last_user_msg: str = "",
        last_assistant_msg: str = "",
    ) -> Optional[str]:
        """检测用户反馈信号，有则记录并返回情感标签"""
        text = user_message.strip()
        if len(text) > 50:
            return None

        sentiment = None
        if self.FEEDBACK_POSITIVE.search(text):
            sentiment = "positive"
        elif self.FEEDBACK_NEGATIVE.search(text):
            sentiment = "negative"

        if not sentiment:
            return None

        context = ""
        if last_user_msg:
            context += f"用户: {last_user_msg[:100]}"
        if last_assistant_msg:
            context += f" → 助手: {last_assistant_msg[:100]}"

        entry = {
            "type": "user_feedback",
            "sentiment": sentiment,
            "feedback_text": text[:100],
            "context": context[:300],
            "category": "user_feedback",
        }
        self.record(entry)
        logger.info(f"用户反馈: {sentiment} — {text[:30]}")
        return sentiment

    # ==================== 模块 4: 工具策略统计 ====================

    def record_tool_execution(
        self,
        tool_name: str,
        success: bool,
        elapsed_ms: int,
    ) -> None:
        stats = self._tool_stats.setdefault(tool_name, {
            "success": 0, "fail": 0, "total_ms": 0, "count": 0,
        })
        if success:
            stats["success"] += 1
        else:
            stats["fail"] += 1
        stats["total_ms"] += elapsed_ms
        stats["count"] += 1

        if stats["count"] % 10 == 0:
            self._save_tool_stats()

    def get_tool_insights(self, tool_names: list[str] | None = None) -> str:
        """生成工具洞察摘要（注入 system prompt 用）"""
        if not self._tool_stats:
            return ""
        lines = []
        for name, s in self._tool_stats.items():
            if tool_names and name not in tool_names:
                continue
            total = s.get("success", 0) + s.get("fail", 0)
            if total < 5:
                continue
            rate = s["success"] / total * 100
            avg_ms = s.get("total_ms", 0) // max(s.get("count", 1), 1)
            if rate < 80:
                lines.append(f"- {name}: 成功率 {rate:.0f}%（{total} 次），注意可能需要换策略")
            elif rate >= 95:
                lines.append(f"- {name}: 成功率 {rate:.0f}%，可靠")
        if not lines:
            return ""
        return "## 工具使用洞察\n" + "\n".join(lines[:8])

    def flush_tool_stats(self) -> None:
        """强制保存统计"""
        self._save_tool_stats()

    def _load_tool_stats(self) -> dict:
        if self._stats_path.exists():
            try:
                return json.loads(self._stats_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_tool_stats(self) -> None:
        try:
            self._stats_path.write_text(
                json.dumps(self._tool_stats, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.debug(f"保存工具统计失败: {e}")

    # ==================== 模块 5: 智能经验召回 ====================

    def recall_relevant_learnings(self, query: str, top_k: int = 3) -> str:
        """从检索后端召回相关经验"""
        try:
            if self._backend.count() == 0:
                return self._fallback_recent_learnings()

            results = self._backend.search(
                query, top_k=top_k, min_score=0.1,
            )
            if not results:
                return self._fallback_recent_learnings()

            lines = []
            for item in results:
                score_pct = int(item.score * 100)
                lines.append(f"- [{score_pct}%] {item.content[:200]}")

            content = "\n".join(lines)
            if len(content) > 1000:
                content = content[:1000] + "\n..."
            from ..agent.memory import redact_secrets
            content = redact_secrets(content)
            return f"## 相关经验\n{content}"
        except Exception:
            return self._fallback_recent_learnings()

    def _fallback_recent_learnings(self) -> str:
        """向量库不可用时退回读 JSONL 最近 5 条"""
        if not self._jsonl_path.exists():
            return ""
        try:
            from collections import deque
            with open(self._jsonl_path, "r", encoding="utf-8") as f:
                recent = list(deque(f, maxlen=5))
            entries = []
            for line in recent:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    text = self._entry_to_text(e)
                    if text:
                        entries.append(f"- {text[:150]}")
                except (json.JSONDecodeError, KeyError):
                    continue
            if not entries:
                return ""
            from ..agent.memory import redact_secrets
            return redact_secrets("## 近期经验\n" + "\n".join(entries))
        except Exception:
            return ""


# ==================== 全局实例管理 ====================

def init_learning_engine(workspace_dir: str | Path) -> LearningEngine:
    global _engine
    _engine = LearningEngine(workspace_dir)
    logger.info(
        f"学习引擎已初始化: {_engine._learnings_dir} (后端: {_engine._backend_type})"
    )
    return _engine


def get_learning_engine() -> Optional[LearningEngine]:
    return _engine
