"""评测数据集：JSON schema 校验与加载。

格式：
{
  "items": [
    {
      "query": "研究问题",
      "relevant_doc_ids": ["doc_a"],
      "docs": [
        {"document_id": "doc_a", "url": "https://...", "title": "...", "text": "..."}
      ]
    }
  ]
}

约定：
- 相关判定在 **document 级**：命中 chunk 的 `document_id ∈ relevant_doc_ids` 即算
  命中，同一文档多个 chunk 只计一次（见 metrics.py）。
- 一个 item 是一个独立知识库：`docs` 全部入库，检索 `query` 后与
  `relevant_doc_ids` 比对；每 (spec, item) 重建独立 store / 词法索引，不串数据。
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EvalDoc:
    document_id: str
    url: str
    title: str
    text: str


@dataclass(frozen=True)
class EvalItem:
    query: str
    relevant_doc_ids: list[str]
    docs: list[EvalDoc]


@dataclass(frozen=True)
class EvalDataset:
    items: list[EvalItem]


def _require_str(data: dict[str, Any], key: str, where: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}.{key} 必须是非空字符串，得到: {value!r}")
    return value


def load_dataset(path: str | Path) -> EvalDataset:
    """加载并校验 JSON 数据集；schema 不合法抛 ValueError（带位置信息）。"""
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取数据集 {path}: {exc}") from exc

    items_raw = raw.get("items")
    if not isinstance(items_raw, list) or not items_raw:
        raise ValueError("数据集必须包含非空的 items 数组")

    items: list[EvalItem] = []
    for i, item_raw in enumerate(items_raw):
        where = f"items[{i}]"
        if not isinstance(item_raw, dict):
            raise ValueError(f"{where} 必须是对象")
        query = _require_str(item_raw, "query", where)

        relevant = item_raw.get("relevant_doc_ids")
        if not isinstance(relevant, list) or not relevant or not all(
            isinstance(d, str) for d in relevant
        ):
            raise ValueError(f"{where}.relevant_doc_ids 必须是非空字符串数组")

        docs_raw = item_raw.get("docs")
        if not isinstance(docs_raw, list) or not docs_raw:
            raise ValueError(f"{where}.docs 必须是非空数组")

        docs: list[EvalDoc] = []
        for j, doc_raw in enumerate(docs_raw):
            doc_where = f"{where}.docs[{j}]"
            if not isinstance(doc_raw, dict):
                raise ValueError(f"{doc_where} 必须是对象")
            docs.append(
                EvalDoc(
                    document_id=_require_str(doc_raw, "document_id", doc_where),
                    url=_require_str(doc_raw, "url", doc_where),
                    title=_require_str(doc_raw, "title", doc_where),
                    text=_require_str(doc_raw, "text", doc_where),
                )
            )

        items.append(EvalItem(query=query, relevant_doc_ids=list(relevant), docs=docs))

    return EvalDataset(items=items)
