"""学习图谱模块（Phase 9）：主题 → 研究 → 学习路径 → 逐点讲解 → 点亮。

对外只暴露 `create_learning_store` 工厂。纯 stdlib sqlite3 + 本地 Markdown，
零重依赖；未启用 `LEARNING_ENABLED`（默认关）时应用照常启动、路由统一 503。
"""

from knowledge_pilot.learning.store import LearningStore


def create_learning_store(db_path: str) -> LearningStore:
    """按路径创建学习存储（自动建父目录）。**每次调用独立实例，调用方负责 close()**。"""
    return LearningStore(db_path)


__all__ = ["LearningStore", "create_learning_store"]
