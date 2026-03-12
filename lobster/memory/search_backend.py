"""
灵雀 - 学习引擎搜索后端

两种实现可通过 LEARNING_BACKEND 环境变量切换:
- sqlite (默认): SQLite FTS5 全文搜索，零依赖，内存 <5MB
- vector: ChromaDB + sentence-transformers 语义搜索，需要 500MB+ 内存
"""

import json
import logging
import sqlite3
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("lingque.learning.backend")


@dataclass
class SearchResult:
    content: str
    score: float
    metadata: dict


class SearchBackend(ABC):
    """学习记录检索后端的统一接口"""

    @abstractmethod
    def add(self, content: str, metadata: dict) -> None: ...

    @abstractmethod
    def search(
        self, query: str, top_k: int = 3, filter_type: str | None = None,
        min_score: float = 0.0,
    ) -> list[SearchResult]: ...

    @abstractmethod
    def count(self) -> int: ...


# ==================== SQLite FTS5 后端 ====================


def _tokenize_chinese(text: str) -> str:
    """中文 2-gram 分词 + 英文按空格分词，用于 FTS5 查询"""
    tokens: list[str] = []
    buf: list[str] = []
    for ch in text:
        if '\u4e00' <= ch <= '\u9fff':
            if buf:
                tokens.append("".join(buf))
                buf = []
            tokens.append(ch)
        elif ch.isalnum():
            buf.append(ch.lower())
        else:
            if buf:
                tokens.append("".join(buf))
                buf = []
    if buf:
        tokens.append("".join(buf))

    chars = [t for t in tokens if len(t) == 1 and '\u4e00' <= t <= '\u9fff']
    words = [t for t in tokens if not (len(t) == 1 and '\u4e00' <= t <= '\u9fff')]
    bigrams = [chars[i] + chars[i + 1] for i in range(len(chars) - 1)]
    all_tokens = words + bigrams + chars[:3]

    seen: set[str] = set()
    deduped: list[str] = []
    for t in all_tokens:
        if t not in seen:
            seen.add(t)
            deduped.append(t)
    return " OR ".join(f'"{t}"' for t in deduped[:15]) if deduped else ""


class SQLiteFTSBackend(SearchBackend):
    """SQLite FTS5 全文检索 — 零依赖，适合小服务器 (2C/4G)"""

    def __init__(self, db_path: str | Path):
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        self._ensure_tables()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
            except Exception:
                conn.close()
                raise
            self._conn = conn
        return self._conn

    def _ensure_tables(self) -> None:
        with self._lock:
            conn = self._get_conn()
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS learnings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    type TEXT DEFAULT '',
                    category TEXT DEFAULT '',
                    source TEXT DEFAULT 'learning_engine',
                    timestamp TEXT DEFAULT '',
                    metadata_json TEXT DEFAULT '{}'
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS learnings_fts
                    USING fts5(content, tokenize='unicode61');
                CREATE INDEX IF NOT EXISTS idx_learnings_type ON learnings(type);
                CREATE INDEX IF NOT EXISTS idx_learnings_source ON learnings(source);
            """)
            conn.commit()

    def add(self, content: str, metadata: dict) -> None:
        if not content or len(content) < 5:
            return
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    "INSERT INTO learnings (content, type, category, source, timestamp, metadata_json) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        content[:500],
                        metadata.get("type", ""),
                        metadata.get("category", ""),
                        metadata.get("source", "learning_engine"),
                        metadata.get("timestamp", datetime.now().isoformat()),
                        json.dumps(metadata, ensure_ascii=False),
                    ),
                )
                rowid = cur.lastrowid
                conn.execute(
                    "INSERT INTO learnings_fts(rowid, content) VALUES (?, ?)",
                    (rowid, content[:500]),
                )
                conn.commit()
            except Exception as e:
                logger.warning(f"SQLite 写入失败: {e}")

    def search(
        self, query: str, top_k: int = 3, filter_type: str | None = None,
        min_score: float = 0.0,
    ) -> list[SearchResult]:
        if not query or len(query) < 2:
            return []
        fts_query = _tokenize_chinese(query)
        if not fts_query:
            return []

        safe_top_k = max(1, min(int(top_k), 50))
        with self._lock:
            conn = self._get_conn()
            try:
                if filter_type:
                    rows = conn.execute(
                        "SELECT l.content, l.metadata_json, fts.rank "
                        "FROM learnings_fts fts "
                        "JOIN learnings l ON l.id = fts.rowid "
                        "WHERE learnings_fts MATCH ? AND l.type = ? "
                        "ORDER BY fts.rank "
                        "LIMIT ?",
                        (fts_query, filter_type, safe_top_k),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT l.content, l.metadata_json, fts.rank "
                        "FROM learnings_fts fts "
                        "JOIN learnings l ON l.id = fts.rowid "
                        "WHERE learnings_fts MATCH ? "
                        "ORDER BY fts.rank "
                        "LIMIT ?",
                        (fts_query, safe_top_k),
                    ).fetchall()

                results = []
                for content, meta_json, rank in rows:
                    score = 1.0 / (1.0 + abs(rank))
                    if score < min_score:
                        continue
                    try:
                        meta = json.loads(meta_json)
                    except (json.JSONDecodeError, TypeError):
                        meta = {}
                    results.append(SearchResult(content=content, score=score, metadata=meta))
                return results
            except Exception as e:
                logger.debug(f"SQLite 搜索失败: {e}")
                return []

    def count(self) -> int:
        try:
            with self._lock:
                row = self._get_conn().execute("SELECT COUNT(*) FROM learnings").fetchone()
            return row[0] if row else 0
        except Exception:
            return 0

    def close(self) -> None:
        """关闭数据库连接"""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


# ==================== 向量库后端 ====================


class VectorSearchBackend(SearchBackend):
    """ChromaDB + sentence-transformers 语义搜索 — 适合大服务器"""

    def add(self, content: str, metadata: dict) -> None:
        try:
            from .vector_store import get_vector_memory
            vm = get_vector_memory()
            if vm is None:
                return
            vm.add(content, metadata=metadata)
        except Exception as e:
            logger.debug(f"向量写入失败: {e}")

    def search(
        self, query: str, top_k: int = 3, filter_type: str | None = None,
        min_score: float = 0.0,
    ) -> list[SearchResult]:
        try:
            from .vector_store import get_vector_memory
            vm = get_vector_memory()
            if vm is None or vm.count() == 0:
                return []
            filter_meta = None
            if filter_type:
                filter_meta = {"type": filter_type}
            items = vm.search(query, top_k=top_k, min_score=min_score,
                              filter_metadata=filter_meta)
            return [
                SearchResult(
                    content=item.content,
                    score=item.score,
                    metadata=item.metadata,
                )
                for item in items
            ]
        except Exception as e:
            logger.debug(f"向量搜索失败: {e}")
            return []

    def count(self) -> int:
        try:
            from .vector_store import get_vector_memory
            vm = get_vector_memory()
            return vm.count() if vm else 0
        except Exception:
            return 0


def create_backend(backend_type: str, db_path: str | Path | None = None) -> SearchBackend:
    """工厂函数：根据类型创建搜索后端"""
    if backend_type == "vector":
        logger.info("学习引擎后端: ChromaDB 向量搜索")
        return VectorSearchBackend()
    else:
        if db_path is None:
            raise ValueError("SQLite 后端需要 db_path 参数")
        logger.info(f"学习引擎后端: SQLite FTS5 ({db_path})")
        return SQLiteFTSBackend(db_path)
