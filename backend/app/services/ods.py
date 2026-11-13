"""贴源层业务规则：状态流转、字段校验与筛选口径都收在这里。

贴源表只允许 草稿 → 建表审批中 → 已上线 → 已下线 单向推进：
重复确认上线、跨级确认上线都会被拦下，不会改变任何状态。
"""
from __future__ import annotations

from typing import Any

from app.services import upstream
from app.store import store

MODULE = "ods"
REQUIRED_FIELDS = ["表编号", "表名称", "来源系统"]
STATUS_ORDER = upstream.ODS_STATUSES
ACTION_RULES = upstream.ODS_ACTION_TARGETS
NEGATIVE_ACTIONS = ["下线表"]


class OdsService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        with store.lock:
            rows = store.rows(MODULE)
            if keyword:
                rows = [row for row in rows if keyword in str(row.get("表编号", ""))]
            if status:
                rows = [row for row in rows if row.get("status") == status]
            total = len(rows)
            start = max(page - 1, 0) * size
            return upstream.annotate_ods_rows(rows[start:start + size]), total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def detail(self, entry_id: int) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """贴源表详情：附上引用它的明细准入结论，供跨入口核对。"""
        with store.lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, []
            downstream = upstream.downstream_for(str(entry.get("表编号", "")))
            return upstream.annotate_ods_rows([entry])[0], downstream

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        code = upstream.normalize_table_code(values.get("表编号"))
        with store.lock:
            if any(upstream.normalize_table_code(row.get("表编号")) == code for row in store.rows(MODULE)):
                return None, []
            rows = store.rows(MODULE)
            entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
            entry["status"] = STATUS_ORDER[0]
            entry["pending"] = True
            entry["abnormal"] = False
            rows.append(entry)
            return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于贴源层可执行范围"
        with store.lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"贴源表 {entry_id} 不存在或已归档"
            current = str(entry.get("status", ""))
            target = ACTION_RULES[action]
            if current == target:
                return None, f"贴源表已处于{target}，重复执行「{action}」不会再次生效"
            if target not in upstream.ODS_TRANSITIONS.get(current, set()):
                return None, (
                    f"贴源表当前为{current or '未知'}，不能直接{action}；"
                    "只能按草稿 → 建表审批中 → 已上线 → 已下线 单向推进"
                )
            with store.transaction():
                entry["status"] = target
                entry["pending"] = target != STATUS_ORDER[-1]
                entry["abnormal"] = action in NEGATIVE_ACTIONS
                affected = upstream.sync_ods_downstream(entry)
                downstream_codes = "、".join(
                    str(relation.get("dwd_code", "")) for relation in affected
                ) or "无"
                upstream.record_audit(
                    operation_table=str(entry.get("表编号", "")),
                    change_type=f"贴源表{action}",
                    before_value=current,
                    after_value=target,
                    reason=(
                        f"贴源表按单向状态流转推进；受影响明细：{downstream_codes}"
                        if action == upstream.TAKEN_OFFLINE
                        else "贴源表状态流转"
                    ),
                )
                for relation in affected:
                    upstream.record_audit(
                        operation_table=str(relation.get("dwd_code", "")),
                        change_type="上游准入结论回写",
                        before_value=upstream.ADMITTED,
                        after_value=(
                            f"{relation.get('准入结论')}（{relation.get('刷新结论')}）"
                        ),
                        reason=(
                            f"上游贴源表 {entry.get('表编号')} 流转为{target}，"
                            "保留历史明细定义但阻止新刷新"
                        ),
                    )
            return entry, f"贴源表已{action}"
