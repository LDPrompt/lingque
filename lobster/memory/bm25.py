"""
BM25 关键词搜索引擎

轻量级实现，无额外依赖。
配合向量搜索做混合检索：向量捕捉语义相似，BM25 捕捉精确匹配。
"""

import re
import math
import logging
from collections import Counter
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("lobster.memory.bm25")


def _tokenize(text: str) -> list[str]:
    """中英文分词（简单但有效）"""
    text = text.lower()
    # 英文: 按空格/标点分割
    # 中文: 逐字 + bigram
    tokens = []
    en_tokens = re.findall(r'[a-zA-Z0-9_]+', text)
    tokens.extend(en_tokens)

    cn_chars = re.findall(r'[\u4e00-\u9fff]', text)
    tokens.extend(cn_chars)
    for i in range(len(cn_chars) - 1):
        tokens.append(cn_chars[i] + cn_chars[i + 1])

    return tokens


@dataclass
class BM25Doc:
    """BM25 索引中的文档"""
    doc_id: str
    content: str
    tokens: list[str]
    tf: Counter


class BM25Index:
    """
    BM25 关键词搜索索引

    用法：
        idx = BM25Index()
        idx.add("doc1", "Python 是一种编程语言")
        idx.add("doc2", "Java 也是编程语言")
        results = idx.search("Python 编程", top_k=5)
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self._docs: dict[str, BM25Doc] = {}
        self._df: Counter = Counter()  # document frequency
        self._avg_dl: float = 0.0
        self._total_docs: int = 0

    def add(self, doc_id: str, content: str):
        """添加文档到索引"""
        tokens = _tokenize(content)
        tf = Counter(tokens)
        doc = BM25Doc(doc_id=doc_id, content=content, tokens=tokens, tf=tf)

        if doc_id in self._docs:
            old_doc = self._docs[doc_id]
            for token in set(old_doc.tokens):
                self._df[token] = max(0, self._df[token] - 1)

        self._docs[doc_id] = doc
        for token in set(tokens):
            self._df[token] += 1

        self._total_docs = len(self._docs)
        total_len = sum(len(d.tokens) for d in self._docs.values())
        self._avg_dl = total_len / self._total_docs if self._total_docs > 0 else 0

    def remove(self, doc_id: str):
        """移除文档"""
        doc = self._docs.pop(doc_id, None)
        if doc:
            for token in set(doc.tokens):
                self._df[token] = max(0, self._df[token] - 1)
            self._total_docs = len(self._docs)
            if self._total_docs > 0:
                total_len = sum(len(d.tokens) for d in self._docs.values())
                self._avg_dl = total_len / self._total_docs
            else:
                self._avg_dl = 0

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float, str]]:
        """
        BM25 搜索

        Returns:
            [(doc_id, score, content), ...] 按分数降序
        """
        if not self._docs:
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        scores = {}
        N = self._total_docs

        for doc_id, doc in self._docs.items():
            score = 0.0
            dl = len(doc.tokens)

            for qt in query_tokens:
                if qt not in doc.tf:
                    continue

                freq = doc.tf[qt]
                df = self._df.get(qt, 0)

                idf = math.log((N - df + 0.5) / (df + 0.5) + 1.0)

                numerator = freq * (self.k1 + 1)
                denominator = freq + self.k1 * (1 - self.b + self.b * dl / self._avg_dl)
                score += idf * numerator / denominator

            if score > 0:
                scores[doc_id] = score

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        return [
            (doc_id, score, self._docs[doc_id].content)
            for doc_id, score in ranked
        ]

    def clear(self):
        """清空索引"""
        self._docs.clear()
        self._df.clear()
        self._avg_dl = 0
        self._total_docs = 0

    @property
    def count(self) -> int:
        return len(self._docs)
