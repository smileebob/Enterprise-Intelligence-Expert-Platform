"""中文分词（jieba）与检索文本工具（技术方案 04 §4.3：应用侧预分词，零额外服务）。"""

from __future__ import annotations

import logging

import jieba

logger = logging.getLogger(__name__)

_STOPWORDS = {"的", "了", "和", "与", "及", "或", "是", "在", "对", "把", "被", "有", "请", "我们", "你们", "吗", "呢", "？", "?", "，", ",", "。", ".", "、", "：", ":", "；", ";", "（", "）", "(", ")", " ", "\n"}


def tokenize(text: str) -> list[str]:
    """搜索模式分词并去停用词（保留数字与字母术语）。"""
    if not text:
        return []
    tokens = [t.strip() for t in jieba.cut_for_search(text) if t.strip()]
    return [t for t in tokens if t not in _STOPWORDS]


def token_text(text: str) -> str:
    """空格分隔的分词文本（写入 kb_chunk.tokens 列）。"""
    return " ".join(tokenize(text))
