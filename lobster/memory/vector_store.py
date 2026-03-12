"""
🐦 灵雀 - P3 向量语义检索

功能:
- ChromaDB 存储对话历史
- sentence-transformers 生成向量
- 语义搜索回忆相关记忆
"""

import logging
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger("lingque.memory.vector")

# 延迟导入，避免未安装时报错
chromadb = None
SentenceTransformer = None


def _patch_sqlite3():
    """如果系统 sqlite3 版本低于 3.35.0，尝试用 pysqlite3 替换"""
    import sqlite3
    if tuple(int(x) for x in sqlite3.sqlite_version.split(".")) >= (3, 35, 0):
        return
    try:
        import pysqlite3 as _pysqlite3
        import sys
        sys.modules["sqlite3"] = _pysqlite3
        logger.info(f"sqlite3 {sqlite3.sqlite_version} 过旧，已替换为 pysqlite3")
    except ImportError:
        logger.warning(
            f"系统 sqlite3 版本 {sqlite3.sqlite_version} < 3.35.0，ChromaDB 可能无法工作。"
            f"请执行: pip install pysqlite3-binary"
        )


def _ensure_deps():
    """确保依赖已安装，并启用离线模式加速加载"""
    global chromadb, SentenceTransformer
    if chromadb is None:
        _patch_sqlite3()

        import os
        if not os.environ.get("HF_HUB_OFFLINE"):
            os.environ["HF_HUB_OFFLINE"] = "1"
            logger.info("已自动启用 HuggingFace 离线模式 (HF_HUB_OFFLINE=1)")

        try:
            import chromadb as _chromadb
            chromadb = _chromadb
        except ImportError:
            raise ImportError("请安装 chromadb: pip install chromadb")

    if SentenceTransformer is None:
        try:
            from sentence_transformers import SentenceTransformer as _ST
            SentenceTransformer = _ST
        except ImportError:
            raise ImportError("请安装 sentence-transformers: pip install sentence-transformers")


@dataclass
class MemoryItem:
    """记忆项"""
    id: str
    content: str
    metadata: dict
    score: float = 0.0
    _embedding: list = None  # 内部缓存，避免 MMR 重新编码


class VectorMemory:
    """
    向量记忆库

    使用 ChromaDB 存储对话历史和重要信息，
    通过语义搜索快速找到相关记忆。

    用法:
        memory = VectorMemory("./memory/vector_db")
        memory.add("用户喜欢Python编程", {"type": "preference"})
        results = memory.search("编程语言偏好", top_k=3)
    """

    def __init__(
        self,
        persist_dir: str | Path = "",
        collection_name: str = "lingque_memory",
        embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2",
    ):
        self.persist_dir = Path(persist_dir).resolve() if persist_dir else Path("./memory/vector_db").resolve()
        self.collection_name = collection_name
        self.embedding_model_name = embedding_model

        self._client = None
        self._collection = None
        self._embedder = None
        self._bm25_index = None
        self._initialized = False
        self._init_lock = __import__("threading").Lock()

    def _init_lazy(self):
        """延迟初始化（首次使用时），线程安全"""
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return

            _ensure_deps()

            self.persist_dir.mkdir(parents=True, exist_ok=True)

            self._client = chromadb.PersistentClient(path=str(self.persist_dir))
            self._collection = self._client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"}
            )

            logger.info(f"加载 Embedding 模型: {self.embedding_model_name}")
            self._embedder = SentenceTransformer(self.embedding_model_name)

            from .bm25 import BM25Index
            self._bm25_index = BM25Index()
            self._sync_bm25_index()

            logger.info("向量记忆库初始化完成 (混合搜索: 向量 70% + BM25 30%)")
            self._initialized = True

    def _sync_bm25_index(self):
        """从 ChromaDB 同步已有文档到 BM25 索引"""
        if not self._bm25_index or not self._collection:
            return
        try:
            count = self._collection.count()
            if count == 0:
                return
            all_docs = self._collection.get(include=["documents"])
            if all_docs["ids"]:
                for doc_id, content in zip(all_docs["ids"], all_docs["documents"]):
                    if content:
                        self._bm25_index.add(doc_id, content)
                logger.info(f"BM25 索引同步完成: {len(all_docs['ids'])} 条文档")
        except Exception as e:
            logger.warning(f"BM25 索引同步失败: {e}")

    def add(
        self,
        content: str,
        metadata: Optional[dict] = None,
        doc_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> str:
        """
        添加记忆

        Args:
            content: 记忆内容
            metadata: 元数据 (如 type, source, timestamp)
            doc_id: 文档 ID (不提供则自动生成)
            user_id: 用户 ID（多用户隔离，单用户可不传）

        Returns:
            doc_id: 文档 ID
        """
        self._init_lazy()

        if doc_id is None:
            doc_id = f"mem_{datetime.now().strftime('%Y%m%d%H%M%S')}_{hash(content) % 10000:04d}"

        meta = dict(metadata) if metadata else {}
        meta["timestamp"] = meta.get("timestamp", datetime.now().isoformat())
        meta["content_length"] = len(content)
        if user_id:
            meta["user_id"] = user_id

        # 生成向量
        embedding = self._embedder.encode(content).tolist()

        self._collection.add(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[content],
            metadatas=[meta],
        )

        if self._bm25_index:
            self._bm25_index.add(doc_id, content)

        logger.debug(f"添加记忆: {doc_id} ({len(content)} 字)")
        return doc_id

    def add_batch(self, items: list[tuple[str, dict]]) -> list[str]:
        """
        批量添加记忆

        Args:
            items: [(content, metadata), ...]

        Returns:
            doc_ids: 文档 ID 列表
        """
        self._init_lazy()

        doc_ids = []
        embeddings = []
        documents = []
        metadatas = []

        for i, (content, meta) in enumerate(items):
            doc_id = f"mem_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
            doc_ids.append(doc_id)

            meta = dict(meta) if meta else {}
            meta["timestamp"] = meta.get("timestamp", datetime.now().isoformat())
            meta["content_length"] = len(content)
            metadatas.append(meta)

            documents.append(content)
            embeddings.append(self._embedder.encode(content).tolist())

        self._collection.add(
            ids=doc_ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )

        if self._bm25_index:
            for did, doc in zip(doc_ids, documents):
                self._bm25_index.add(did, doc)

        logger.info(f"批量添加 {len(doc_ids)} 条记忆")
        return doc_ids

    def search(
        self,
        query: str,
        top_k: int = 5,
        filter_metadata: Optional[dict] = None,
        min_score: float = 0.0,
        user_id: Optional[str] = None,
    ) -> list[MemoryItem]:
        """
        混合搜索记忆（向量 70% + BM25 30% + 时间衰减 + MMR 去冗余）

        流程: 向量搜索 + BM25 → 加权合并 → 时间衰减 → MMR 去冗余

        Args:
            query: 查询文本
            top_k: 返回数量
            filter_metadata: 元数据过滤条件
            min_score: 最小相似度 (0-1)
            user_id: 用户 ID（传入则只搜索该用户的记忆）

        Returns:
            匹配的记忆列表
        """
        self._init_lazy()

        if user_id:
            filter_metadata = dict(filter_metadata) if filter_metadata else {}
            filter_metadata["user_id"] = user_id

        # 向量搜索
        vector_results = self._vector_search(query, top_k * 3, filter_metadata)

        # BM25 关键词搜索
        bm25_results = self._bm25_search(query, top_k * 3)

        # 合并结果 (向量 70% + BM25 30%)
        merged = self._merge_results(vector_results, bm25_results,
                                      vector_weight=0.7, bm25_weight=0.3)

        # 时间衰减：近期记忆权重更高
        merged = self._apply_time_decay(merged)

        # MMR 去冗余
        merged = self._mmr_rerank(query, merged, top_k, lambda_param=0.7)

        items = [item for item in merged if item.score >= min_score]
        return items[:top_k]

    def _vector_search(
        self,
        query: str,
        top_k: int,
        filter_metadata: Optional[dict] = None,
    ) -> list[MemoryItem]:
        """纯向量搜索（同时拉取 embeddings 供 MMR 复用）"""
        query_embedding = self._embedder.encode(query).tolist()

        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=filter_metadata,
            include=["documents", "metadatas", "distances", "embeddings"],
        )

        items = []
        if results["ids"] and results["ids"][0]:
            embeddings = results.get("embeddings")
            for i, doc_id in enumerate(results["ids"][0]):
                distance = results["distances"][0][i] if results["distances"] else 0
                score = 1 - distance

                emb = embeddings[0][i] if embeddings and embeddings[0] else None
                items.append(MemoryItem(
                    id=doc_id,
                    content=results["documents"][0][i],
                    metadata=results["metadatas"][0][i] if results["metadatas"] else {},
                    score=score,
                    _embedding=emb,
                ))

        return items

    def _bm25_search(self, query: str, top_k: int) -> list[MemoryItem]:
        """BM25 关键词搜索"""
        if not self._bm25_index:
            return []

        results = self._bm25_index.search(query, top_k=top_k)
        if not results:
            return []

        max_score = results[0][1] if results else 1.0
        items = []
        for doc_id, score, content in results:
            normalized = score / max_score if max_score > 0 else 0
            items.append(MemoryItem(
                id=doc_id,
                content=content,
                metadata={},
                score=normalized,
            ))
        return items

    @staticmethod
    def _apply_time_decay(items: list[MemoryItem], half_life_days: float = 30.0) -> list[MemoryItem]:
        """时间衰减：近期记忆权重更高，半衰期 30 天"""
        now = datetime.now()
        for item in items:
            ts_str = item.metadata.get("timestamp", "")
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str)
                    age_days = (now - ts).total_seconds() / 86400.0
                    decay = 0.5 ** (age_days / half_life_days)
                    # 混合：70% 原始分 + 30% 时间衰减
                    item.score = item.score * 0.7 + item.score * decay * 0.3
                except (ValueError, TypeError):
                    pass
        items.sort(key=lambda x: x.score, reverse=True)
        return items

    def _mmr_rerank(
        self,
        query: str,
        items: list[MemoryItem],
        top_k: int,
        lambda_param: float = 0.7,
    ) -> list[MemoryItem]:
        """MMR (Maximal Marginal Relevance) 重排序，平衡相关性和多样性

        lambda=1.0 纯相关性，lambda=0.0 纯多样性，默认 0.7 偏相关性
        """
        if len(items) <= top_k or not self._embedder:
            return items

        try:
            import numpy as np
        except ImportError:
            return items[:top_k]

        query_emb = self._embedder.encode(query)
        doc_embs = [
            np.array(item._embedding) if item._embedding is not None
            else self._embedder.encode(item.content)
            for item in items
        ]

        def cosine_sim(a, b):
            dot = np.dot(a, b)
            norm = np.linalg.norm(a) * np.linalg.norm(b)
            return dot / norm if norm > 0 else 0.0

        selected_indices = []
        remaining = list(range(len(items)))

        while len(selected_indices) < top_k and remaining:
            best_idx = -1
            best_score = -1.0

            for idx in remaining:
                relevance = cosine_sim(query_emb, doc_embs[idx])

                max_sim = 0.0
                for sel_idx in selected_indices:
                    sim = cosine_sim(doc_embs[idx], doc_embs[sel_idx])
                    max_sim = max(max_sim, sim)

                mmr_score = lambda_param * relevance - (1 - lambda_param) * max_sim

                if mmr_score > best_score:
                    best_score = mmr_score
                    best_idx = idx

            if best_idx >= 0:
                selected_indices.append(best_idx)
                remaining.remove(best_idx)
            else:
                break

        return [items[i] for i in selected_indices]

    @staticmethod
    def _merge_results(
        vector_items: list[MemoryItem],
        bm25_items: list[MemoryItem],
        vector_weight: float = 0.7,
        bm25_weight: float = 0.3,
    ) -> list[MemoryItem]:
        """合并向量和 BM25 结果，加权去重"""
        scores: dict[str, float] = {}
        content_map: dict[str, MemoryItem] = {}

        for item in vector_items:
            scores[item.id] = vector_weight * item.score
            content_map[item.id] = item

        for item in bm25_items:
            if item.id in scores:
                scores[item.id] += bm25_weight * item.score
            else:
                scores[item.id] = bm25_weight * item.score
                content_map[item.id] = item

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        result = []
        for doc_id, score in ranked:
            item = content_map[doc_id]
            item.score = score
            result.append(item)
        return result

    def delete(self, doc_id: str):
        """删除记忆"""
        self._init_lazy()
        self._collection.delete(ids=[doc_id])
        if self._bm25_index:
            self._bm25_index.remove(doc_id)
        logger.debug(f"删除记忆: {doc_id}")

    def clear(self):
        """清空所有记忆"""
        self._init_lazy()
        self._client.delete_collection(self.collection_name)
        self._collection = self._client.create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"}
        )
        if self._bm25_index:
            self._bm25_index.clear()
        logger.info("已清空向量记忆库")

    def count(self) -> int:
        """获取记忆数量"""
        self._init_lazy()
        return self._collection.count()

    def get_stats(self) -> dict:
        """获取统计信息"""
        self._init_lazy()
        return {
            "collection": self.collection_name,
            "count": self._collection.count(),
            "embedding_model": self.embedding_model_name,
            "persist_dir": str(self.persist_dir),
        }


# ==================== 技能注册 ====================

_vector_memory: Optional[VectorMemory] = None


def init_vector_memory(persist_dir: str | Path, preload: bool = True):
    """初始化全局向量记忆库（由 main.py 调用，传入绝对路径）"""
    global _vector_memory
    _vector_memory = VectorMemory(persist_dir)
    logger.info(f"向量记忆库路径: {_vector_memory.persist_dir}")

    if preload:
        import threading

        def _bg_init():
            try:
                _vector_memory._init_lazy()
                logger.info("向量记忆库后台预热完成 ✓")
            except Exception as e:
                logger.warning(f"向量记忆库后台预热失败: {e}")

        t = threading.Thread(target=_bg_init, daemon=True, name="vector-preload")
        t.start()
        logger.info("向量记忆库后台预热已启动...")


def get_vector_memory() -> Optional[VectorMemory]:
    """获取全局向量记忆库实例（未初始化则返回 None）"""
    return _vector_memory
