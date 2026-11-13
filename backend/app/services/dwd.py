"""明细层业务规则：状态流转、字段校验与上游准入都收在这里。

明细保存上游前先确认目标贴源表已上线且来源系统一致；同一条上游关系并发登记时
只允许一次生效，失败不会留下只更新一侧的中间状态。
"""
from __future__ import annotations

from typing import Any

from app.services import upstream as upstream_module
from app.services.upstream import evaluate, find_ods, normalize_table_code
from app.store import store

MODULE = "dwd"
REQUIRED_FIELDS = ["表编号", "表名称", "业务域"]
STATUS_ORDER = ["草稿", "建表审批中", "已上线", "已下线"]
ACTION_RULES = upstream_module.DWD_ACTION_TARGETS
NEGATIVE_ACTIONS = ["下线表"]
UPSTREAM_FIELD = "上游贴源表"


class DwdService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        upstream: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        with store.lock:
            return self._list_entries(keyword=keyword, status=status, upstream=upstream, page=page, size=size)

    def _list_entries(
        self,
        *,
        keyword: str | None,
        status: str | None,
        upstream: str | None,
        page: int,
        size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("表编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if upstream:
            wanted = normalize_table_code(upstream)
            rows = [
                row for row in rows
                if normalize_table_code(row.get(UPSTREAM_FIELD)) == wanted
            ]
        total = len(rows)
        start = max(page - 1, 0) * size
        visible = upstream_module.annotate_dwd_rows(rows[start:start + size])
        return visible, total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def detail(self, entry_id: int) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """明细详情：把上游准入结论一并带出，和贴源列表保持同一口径。"""
        with store.lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, None
            return upstream_module.annotate_dwd_rows([entry])[0], upstream_module.relation_for(entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str], str]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing, ""
        code = normalize_table_code(values.get("表编号"))
        with store.lock:
            if any(normalize_table_code(row.get("表编号")) == code for row in store.rows(MODULE)):
                return None, [], f"明细表编号 {code} 已存在，不能重复登记"
            entry = {"id": max((int(row.get("id", 0)) for row in store.rows(MODULE)), default=0) + 1}
            entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
            for field in ("主键列", "度量列", "时间分区列", "刷新水位"):
                if values.get(field) is not None:
                    entry[field] = values.get(field)
            entry["status"] = STATUS_ORDER[0]
            entry["pending"] = True
            entry["abnormal"] = False
            upstream_code = normalize_table_code(values.get(UPSTREAM_FIELD))
            entry[UPSTREAM_FIELD] = upstream_code or None
            if not upstream_code:
                store.rows(MODULE).append(entry)
                return entry, [], ""
            with store.transaction():
                target = find_ods(upstream_code)
                verdict = evaluate(target, entry, upstream_code)
                if not verdict["生效"]:
                    upstream_module.record_audit(
                        operation_table=code,
                        change_type="明细上游准入失败",
                        before_value="未登记上游",
                        after_value=f"{upstream_code}｜{verdict['准入结论']}（{verdict['准入说明']}）",
                        reason="明细登记时上游贴源表未通过准入，登记未落库",
                    )
                    return None, [], verdict["准入说明"]
                store.rows(MODULE).append(entry)
                relation = upstream_module.upsert_record(
                    dwd_row=entry,
                    ods_row=target,
                    upstream_code=upstream_code,
                )
                upstream_module.record_audit(
                    operation_table=code,
                    change_type="明细上游准入通过",
                    before_value="未登记上游",
                    after_value=upstream_module.upstream_state_label(relation),
                    reason="明细表登记并保存上游贴源表，准入通过后允许刷新",
                )
            return entry, [], ""

    def save_upstream(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """登记/变更明细的上游贴源表。

        先算准入结论，通过才落库；整段写入包在事务里，失败不会只更新一侧。
        重复提交同一条已生效的关系只返回既有结论，不重复生效。
        """
        upstream_code = normalize_table_code(values.get(UPSTREAM_FIELD))
        if not upstream_code:
            return None, "请填写上游贴源表，明细表保存上游前必须确认准入结论"
        with store.lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"明细表 {entry_id} 不存在或已归档"
            code = str(entry.get("表编号", ""))
            existing = upstream_module.relation_for(entry_id)
            target = find_ods(upstream_code)
            verdict = evaluate(target, entry, upstream_code)
            if not verdict["生效"]:
                with store.transaction():
                    upstream_module.record_audit(
                        operation_table=code,
                        change_type="明细上游准入失败",
                        before_value=upstream_module.upstream_state_label(existing),
                        after_value=f"{upstream_code}｜{verdict['准入结论']}（{verdict['准入说明']}）",
                        reason="明细保存上游前准入校验未通过，关系未落库",
                    )
                return None, verdict["准入说明"]
            if (
                existing is not None
                and normalize_table_code(existing.get("上游贴源表")) == upstream_code
                and existing.get("已提交")
            ):
                return entry, f"明细表上游 {upstream_code} 已生效，重复登记不会再次生效"
            with store.transaction():
                relation = upstream_module.upsert_record(dwd_row=entry, ods_row=target, upstream_code=upstream_code)
                entry[UPSTREAM_FIELD] = upstream_code
                entry["刷新水位"] = entry.get("刷新水位") or "待首次刷新"
                upstream_module.record_audit(
                    operation_table=code,
                    change_type="明细上游关系变更",
                    before_value=upstream_module.upstream_state_label(existing),
                    after_value=upstream_module.upstream_state_label(relation),
                    reason="明细保存上游贴源表，准入通过后生效",
                )
            return entry, f"明细表上游 {upstream_code} 准入通过，已生效"

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于明细层可执行范围"
        with store.lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"明细表 {entry_id} 不存在或已归档"
            current = str(entry.get("status", ""))
            target = ACTION_RULES[action]
            if current == target:
                return None, f"明细表已处于{target}，重复执行「{action}」不会再次生效"
            if target not in upstream_module.DWD_TRANSITIONS.get(current, set()):
                return None, (
                    f"明细表当前为{current or '未知'}，不能直接{action}；"
                    "只能按草稿 → 建表审批中 → 已上线 → 已下线 单向推进"
                )
            entry["status"] = target
            entry["pending"] = target != STATUS_ORDER[-1]
            entry["abnormal"] = action in NEGATIVE_ACTIONS
            return entry, f"明细表已{action}"
