"""RAG 文档与分块的数据结构。

Document 表示一篇抓取到的网页资料；Chunk 是分块后的最小检索单元。
Chunk 保留来源元数据（url / title / source），检索命中后可直接回溯出处做引用。
"""

from dataclasses import dataclass, field
from hashlib import sha1


@dataclass
class Document:
    """一篇待入库的文档（当前来自网页抓取；预留用户上传 / 论文等 source）。"""

    document_id: str
    source: str  # "web"（预留 "user" 等）
    url: str
    title: str
    text: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class Chunk:
    """一条可检索的分块。"""

    document_id: str
    chunk_id: str  # f"{document_id}:{index}"
    source: str
    url: str
    title: str
    text: str
    metadata: dict[str, str] = field(default_factory=dict)  # {"chunk_index", "chunk_total"}


def make_document_id(url: str) -> str:
    """由 URL 生成稳定去重键：sha1(url) 前 12 位。"""
    return sha1(url.encode("utf-8")).hexdigest()[:12]
