"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
"""
from __future__ import annotations

import copy
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from app.seed import SEED_ROWS


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        # 贴源表与明细表之间的上游准入关系单独存放：它既是两处写入路径的唯一事实源，
        # 也是并发登记时按提交顺序仲裁的依据。关系不参与运营概览统计。
        self._relations: list[dict[str, Any]] = []
        self._commit_seq = 0
        self._lock = threading.RLock()

    @property
    def lock(self) -> threading.RLock:
        """跨服务共享的可重入锁；所有状态变更都必须串行化。"""
        return self._lock

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def relation_rows(self) -> list[dict[str, Any]]:
        """上游准入关系表；只能通过 transaction 修改，保证两侧写入同生共死。"""
        return self._relations

    def next_commit_seq(self) -> int:
        """单调递增的提交序号，用来判定“先提交的完整记录”。"""
        self._commit_seq += 1
        return self._commit_seq

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """把贴源表状态、明细上游关系和审计记录包成一个原子操作。

        任一步骤抛出异常都会回滚整个快照，不会留下只更新一侧的中间状态。
        """
        with self._lock:
            snapshot_tables = copy.deepcopy(self._tables)
            snapshot_relations = copy.deepcopy(self._relations)
            snapshot_seq = self._commit_seq
            try:
                yield
            except Exception:
                self._tables = snapshot_tables
                self._relations = snapshot_relations
                self._commit_seq = snapshot_seq
                raise

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
