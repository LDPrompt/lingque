"""
用户画像管理 — 结构化用户理解 + 情绪感知 + 成长追踪

核心能力:
- 结构化存储用户偏好、习惯、技术水平、兴趣
- 实时情绪检测（从消息文本推断情绪状态）
- 成长里程碑追踪（自动发现并记录用户成长节点）
- 与知识图谱联动（用户实体作为图谱核心节点）
"""

import json
import logging
import os
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from pathlib import Path
from typing import Optional

logger = logging.getLogger("lingque.memory.profile")


# ────────────────────── 情绪检测 ──────────────────────

EMOTION_PATTERNS: dict[str, list[re.Pattern]] = {
    "frustrated": [
        re.compile(r"(不行|不好使|又出|还是不|怎么又|搞不|烦|崩溃|废了|为什么又|气死|无语|坑|糟糕|太慢|受不了|搞什么)", re.I),
        re.compile(r"(不是说|都说了|说了多少遍|怎么回事|又来了|又挂了|又报错|又崩|又断)", re.I),
        re.compile(r"[!！]{2,}.*[?？]|[?？]{2,}"),
    ],
    "confused": [
        re.compile(r"(看不懂|不明白|不理解|什么意思|啥意思|怎么理解|为什么|不清楚|迷糊|搞不清)", re.I),
        re.compile(r"(这是什么|这啥|怎么算|怎么填|哪个|区别是)", re.I),
        re.compile(r"[?？]{2,}"),
    ],
    "happy": [
        re.compile(r"(太好了|不错|可以|棒|厉害|牛|好用|完美|赞|nice|great|awesome|感谢|谢谢|辛苦|很好|终于)", re.I),
        re.compile(r"(成功了|搞定|解决了|跑起来|好了|通了|生效了)", re.I),
        re.compile(r"[!！].*[好棒赞]|哈哈|嘿嘿|😊|👍|🎉"),
    ],
    "excited": [
        re.compile(r"(太棒了|太牛了|太强了|绝了|全都要|全做|一起做|赶紧|快|马上|等不及)", re.I),
        re.compile(r"[!！]{2,}"),
    ],
    "anxious": [
        re.compile(r"(来不及|赶紧|着急|紧急|马上要|deadline|急|快点|加急|时间不够)", re.I),
        re.compile(r"(比赛|上线|演示|汇报|明天就)", re.I),
    ],
    "neutral": [],
}


def detect_emotion(text: str) -> tuple[str, float]:
    """
    Detect the dominant emotion from user text.
    Returns (emotion, confidence).
    """
    if not text or len(text) < 2:
        return "neutral", 0.5

    scores: dict[str, float] = {}
    for emotion, patterns in EMOTION_PATTERNS.items():
        if not patterns:
            continue
        score = 0.0
        for pat in patterns:
            matches = pat.findall(text)
            score += len(matches) * 0.3
        if score > 0:
            scores[emotion] = min(score, 1.0)

    if not scores:
        return "neutral", 0.6

    best = max(scores, key=scores.get)
    return best, scores[best]


EMOTION_RESPONSE_HINTS: dict[str, str] = {
    "frustrated": "用户可能有些沮丧，请保持耐心、表达理解，主动提出解决方案，不要推卸问题",
    "confused": "用户可能有些困惑，请用简单明了的语言解释，必要时举例说明，不要假设用户已经懂了",
    "happy": "用户心情不错，可以适当互动回应，保持积极氛围",
    "excited": "用户很兴奋，配合用户的热情，积极推进",
    "anxious": "用户可能比较着急，直奔主题、先给方案再解释原因，减少不必要的铺垫",
    "neutral": "正常交流即可",
}


# ────────────────────── 里程碑定义 ──────────────────────

MILESTONE_DEFS = [
    {"id": "first_task", "condition": lambda s: s.get("total", 0) >= 1,
     "title": "初次使命", "desc": "完成了第一个任务"},
    {"id": "task_10", "condition": lambda s: s.get("total", 0) >= 10,
     "title": "渐入佳境", "desc": "已经一起完成了 10 个任务"},
    {"id": "task_50", "condition": lambda s: s.get("total", 0) >= 50,
     "title": "默契搭档", "desc": "50 个任务！我们配合越来越好了"},
    {"id": "task_100", "condition": lambda s: s.get("total", 0) >= 100,
     "title": "百战老兵", "desc": "100 个任务！你已经是我最信赖的搭档"},
    {"id": "task_500", "condition": lambda s: s.get("total", 0) >= 500,
     "title": "传奇伙伴", "desc": "500 个任务，一路走来感谢有你"},
    {"id": "first_browser", "condition": lambda s: s.get("categories", {}).get("browser", 0) >= 1,
     "title": "网页探索者", "desc": "第一次使用浏览器自动化"},
    {"id": "first_code", "condition": lambda s: s.get("categories", {}).get("code", 0) >= 1,
     "title": "代码新手", "desc": "第一次执行代码"},
    {"id": "streak_7", "condition": lambda s: s.get("streak_days", 0) >= 7,
     "title": "连续七天", "desc": "连续 7 天使用，已经养成习惯了"},
    {"id": "streak_30", "condition": lambda s: s.get("streak_days", 0) >= 30,
     "title": "月度达人", "desc": "连续使用 30 天，你的坚持令人敬佩"},
]


# ────────────────────── UserProfile ──────────────────────

@dataclass
class TaskStats:
    total: int = 0
    categories: dict[str, int] = field(default_factory=dict)
    streak_days: int = 0
    last_active: str = ""
    first_active: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TaskStats":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Milestone:
    id: str
    title: str
    description: str
    achieved_at: str = ""
    notified: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Milestone":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class UserProfile:
    user_id: str = ""
    name: str = ""
    communication_style: str = "auto"
    tech_level: str = "intermediate"
    interests: list[str] = field(default_factory=list)
    goals: list[str] = field(default_factory=list)
    current_mood: str = "neutral"
    mood_confidence: float = 0.5
    task_stats: TaskStats = field(default_factory=TaskStats)
    milestones: list[Milestone] = field(default_factory=list)
    preferences: dict = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "UserProfile":
        ts = d.pop("task_stats", {})
        ms = d.pop("milestones", [])
        safe = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        prof = cls(**safe)
        if isinstance(ts, dict):
            prof.task_stats = TaskStats.from_dict(ts)
        if isinstance(ms, list):
            prof.milestones = [Milestone.from_dict(m) if isinstance(m, dict) else m for m in ms]
        return prof

    def days_together(self) -> int:
        if not self.created_at:
            return 0
        try:
            created = datetime.fromisoformat(self.created_at).date()
            return (date.today() - created).days
        except Exception:
            return 0

    def achieved_milestone_ids(self) -> set[str]:
        return {m.id for m in self.milestones}


# ────────────────────── ProfileManager ──────────────────────

class UserProfileManager:
    """Manages per-user profiles with persistence."""

    def __init__(self, memory_dir: str | Path):
        self._base = Path(memory_dir)
        self._cache: dict[str, UserProfile] = {}

    def _profile_path(self, user_id: str) -> Path:
        user_dir = self._base / "users" / user_id
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir / "profile.json"

    def get(self, user_id: str) -> UserProfile:
        if user_id in self._cache:
            return self._cache[user_id]

        path = self._profile_path(user_id)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                profile = UserProfile.from_dict(data)
            except Exception as e:
                logger.warning(f"加载用户画像失败 ({user_id}): {e}")
                profile = UserProfile(user_id=user_id)
        else:
            profile = UserProfile(user_id=user_id)

        if not profile.created_at:
            profile.created_at = datetime.now().isoformat()
        profile.user_id = user_id
        self._cache[user_id] = profile
        return profile

    def save(self, profile: UserProfile):
        profile.updated_at = datetime.now().isoformat()
        path = self._profile_path(profile.user_id)
        try:
            path.write_text(
                json.dumps(profile.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"保存用户画像失败: {e}")
        self._cache[profile.user_id] = profile

    # ──── 情绪更新 ────

    def update_mood(self, user_id: str, text: str) -> tuple[str, float]:
        profile = self.get(user_id)
        emotion, conf = detect_emotion(text)
        if conf > 0.3:
            profile.current_mood = emotion
            profile.mood_confidence = conf
        return emotion, conf

    # ──── 任务统计 ────

    def record_task(self, user_id: str, category: str = "general"):
        profile = self.get(user_id)
        stats = profile.task_stats
        stats.total += 1
        stats.categories[category] = stats.categories.get(category, 0) + 1

        today = date.today().isoformat()
        if not stats.first_active:
            stats.first_active = today

        if stats.last_active:
            try:
                last = date.fromisoformat(stats.last_active)
                diff = (date.today() - last).days
                if diff == 1:
                    stats.streak_days += 1
                elif diff > 1:
                    stats.streak_days = 1
            except Exception:
                stats.streak_days = 1
        else:
            stats.streak_days = 1

        stats.last_active = today
        self.save(profile)

    # ──── 里程碑检查 ────

    def check_milestones(self, user_id: str) -> list[Milestone]:
        profile = self.get(user_id)
        achieved = profile.achieved_milestone_ids()
        stats_dict = profile.task_stats.to_dict()
        new_milestones = []

        for mdef in MILESTONE_DEFS:
            if mdef["id"] in achieved:
                continue
            try:
                if mdef["condition"](stats_dict):
                    ms = Milestone(
                        id=mdef["id"],
                        title=mdef["title"],
                        description=mdef["desc"],
                        achieved_at=datetime.now().isoformat(),
                        notified=False,
                    )
                    profile.milestones.append(ms)
                    new_milestones.append(ms)
            except Exception:
                continue

        if new_milestones:
            self.save(profile)
        return new_milestones

    def get_unnotified_milestones(self, user_id: str) -> list[Milestone]:
        profile = self.get(user_id)
        unnotified = [m for m in profile.milestones if not m.notified]
        return unnotified

    def mark_milestones_notified(self, user_id: str):
        profile = self.get(user_id)
        changed = False
        for m in profile.milestones:
            if not m.notified:
                m.notified = True
                changed = True
        if changed:
            self.save(profile)

    # ──── 用户画像更新（从对话中提取） ────

    def update_from_message(self, user_id: str, text: str):
        """Light-weight extraction from user message (no LLM call)."""
        profile = self.get(user_id)
        changed = False

        name_patterns = [
            re.compile(r"(?:我是|我叫|叫我|称呼我)[\s]*([^\s,，。！？]{2,8})"),
        ]
        for pat in name_patterns:
            m = pat.search(text)
            if m and not profile.name:
                profile.name = m.group(1)
                changed = True

        interest_kw = {
            "电商": ["淘宝", "京东", "闲鱼", "亚马逊", "电商", "商品", "店铺"],
            "编程": ["代码", "编程", "python", "javascript", "开发", "程序"],
            "AI": ["AI", "模型", "大模型", "GPT", "LLM", "智能"],
            "数据": ["数据", "爬虫", "采集", "抓取", "分析", "报表"],
            "自动化": ["自动化", "RPA", "脚本", "批量", "定时"],
            "设计": ["设计", "UI", "美工", "图片", "logo"],
        }
        for interest, keywords in interest_kw.items():
            if interest not in profile.interests:
                if any(kw in text for kw in keywords):
                    profile.interests.append(interest)
                    changed = True
                    if len(profile.interests) > 10:
                        profile.interests = profile.interests[-10:]

        if changed:
            self.save(profile)

    # ──── 生成上下文摘要 ────

    def build_context_summary(self, user_id: str) -> str:
        profile = self.get(user_id)
        parts = []

        display_name = profile.name or user_id or "用户"
        days = profile.days_together()
        total = profile.task_stats.total

        if days > 0 or total > 0:
            companion = f"你已陪伴 {display_name} {days} 天" if days > 0 else ""
            if companion and total > 0:
                tasks_info = f"，一起完成了 {total} 个任务"
            elif total > 0:
                tasks_info = f"已一起完成了 {total} 个任务"
            else:
                tasks_info = ""
            parts.append(f"{companion}{tasks_info}。")

        if profile.interests:
            parts.append(f"{display_name} 关注的领域: {', '.join(profile.interests[:5])}")

        if profile.goals:
            parts.append(f"当前目标: {', '.join(profile.goals[:3])}")

        tech_desc = {
            "beginner": "技术新手，请用通俗易懂的语言",
            "intermediate": "有一定技术基础",
            "advanced": "技术能力强，可以用专业术语",
        }
        if profile.tech_level in tech_desc:
            parts.append(tech_desc[profile.tech_level])

        mood = profile.current_mood
        if mood != "neutral" and mood in EMOTION_RESPONSE_HINTS:
            parts.append(f"当前情绪: {mood} — {EMOTION_RESPONSE_HINTS[mood]}")

        streak = profile.task_stats.streak_days
        if streak >= 3:
            parts.append(f"已连续使用 {streak} 天")

        cats = profile.task_stats.categories
        if cats:
            top = sorted(cats.items(), key=lambda x: x[1], reverse=True)[:3]
            top_str = ", ".join(f"{k}({v}次)" for k, v in top)
            parts.append(f"常用领域: {top_str}")

        return "\n".join(parts) if parts else ""

    def build_milestone_greeting(self, user_id: str) -> str:
        unnotified = self.get_unnotified_milestones(user_id)
        if not unnotified:
            return ""
        lines = []
        for m in unnotified:
            lines.append(f"[里程碑达成] {m.title}: {m.description}")
        self.mark_milestones_notified(user_id)
        return "\n".join(lines)


# ────────────────────── global instance ──────────────────────

_manager: Optional[UserProfileManager] = None


def get_profile_manager() -> Optional[UserProfileManager]:
    return _manager


def init_profile_manager(memory_dir: str | Path) -> UserProfileManager:
    global _manager
    _manager = UserProfileManager(memory_dir)
    return _manager
