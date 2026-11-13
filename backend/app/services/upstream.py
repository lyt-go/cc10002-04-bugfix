"""贴源表与明细表共享的上游准入规则。

贴源表详情、明细登记、明细详情和变更审计都从这里取同一个结论，避免两处各算一套：
- 贴源表只能按 草稿 → 建表审批中 → 已上线 → 已下线 单向推进；
- 明细保存上游前，目标贴源表必须已上线，且来源系统与明细表一致；
- 贴源表后续下线会回写上游准入结论并阻止新的刷新，但保留历史明细定义；
- 上游关系冲突时按先提交的完整记录仲裁，界面临时值不能覆盖已提交关系。
"""
from __future__ import annotations

from typing import Any

from app.store import store

ODS_MODULE = "ods"
DWD_MODULE = "dwd"

ODS_STATUSES = ["草稿", "建表审批中", "已上线", "已下线"]
ODS_ACTION_TARGETS = {"提交建表审批": "建表审批中", "确认上线": "已上线", "下线表": "已下线"}
# 允许的边：草稿→建表审批中→已上线→已下线；确认上线只能从建表审批中进入。
ODS_TRANSITIONS = {
    "草稿": {"建表审批中"},
    "建表审批中": {"已上线"},
    "已上线": {"已下线"},
    "已下线": set(),
}
# 明细层沿用同一套单向推进口径，避免两个入口的“可执行动作”不一致。
DWD_ACTION_TARGETS = {"提交建表审批": "建表审批中", "确认上线": "已上线", "下线表": "已下线"}
DWD_TRANSITIONS = {
    "草稿": {"建表审批中"},
    "建表审批中": {"已上线"},
    "已上线": {"已下线"},
    "已下线": set(),
}
CONFIRM_ONLINE = "确认上线"
TAKEN_OFFLINE = "下线表"

ADMITTED = "准入通过"
BLOCKED = "准入失败"
INACTIVE = "已下线"
REFRESH_READY = "允许刷新"
REFRESH_BLOCKED = "禁止刷新"


def normalize_table_code(value: Any) -> str:
    return str(value or "").strip().upper()


def normalize_system(value: Any) -> str:
    return str(value or "").strip()


def find_ods(code: str) -> dict[str, Any] | None:
    if not code:
        return None
    for row in store.rows(ODS_MODULE):
        if normalize_table_code(row.get("表编号")) == code:
            return row
    return None


def relation_rows() -> list[dict[str, Any]]:
    """已提交的上游准入关系；未生效的登记不会进入这里。"""
    return [relation for relation in store.relation_rows() if relation.get("已提交")]


def relation_for(dwd_id: int) -> dict[str, Any] | None:
    for relation in relation_rows():
        if int(relation.get("dwd_id", 0)) == dwd_id:
            return relation
    return None


def evaluate(ods_row: dict[str, Any] | None, dwd_row: dict[str, Any], upstream_code: str) -> dict[str, Any]:
    """计算一条上游关系的准入结论；调用方负责在同一把锁内落库。"""
    if ods_row is None:
        return {
            "准入结论": BLOCKED,
            "上游状态": "不存在",
            "准入说明": f"上游贴源表 {upstream_code or '（空）'} 不存在，明细表不能引用",
            "刷新结论": REFRESH_BLOCKED,
            "来源系统": "",
            "生效": False,
        }
    source_system = normalize_system(ods_row.get("来源系统"))
    dwd_system = normalize_system(dwd_row.get("业务域"))
    if normalize_system(ods_row.get("表编号")) != upstream_code:
        return {
            "准入结论": BLOCKED,
            "上游状态": str(ods_row.get("status", "")),
            "准入说明": f"上游贴源表编号 {upstream_code} 与登记记录不一致",
            "刷新结论": REFRESH_BLOCKED,
            "来源系统": source_system,
            "生效": False,
        }
    status = str(ods_row.get("status", ""))
    if status == "已下线":
        return {
            "准入结论": INACTIVE,
            "上游状态": status,
            "准入说明": f"上游贴源表 {upstream_code} 已下线，保留历史明细定义但不再允许新刷新",
            "刷新结论": REFRESH_BLOCKED,
            "来源系统": source_system,
            "生效": False,
        }
    if status != "已上线":
        return {
            "准入结论": BLOCKED,
            "上游状态": status,
            "准入说明": f"上游贴源表 {upstream_code} 当前为{status}，尚未上线，明细表不能引用",
            "刷新结论": REFRESH_BLOCKED,
            "来源系统": source_system,
            "生效": False,
        }
    if not dwd_system:
        return {
            "准入结论": BLOCKED,
            "上游状态": status,
            "准入说明": "明细表缺少业务域（来源系统），无法与上游贴源表核对",
            "刷新结论": REFRESH_BLOCKED,
            "来源系统": source_system,
            "生效": False,
        }
    if dwd_system != source_system:
        return {
            "准入结论": BLOCKED,
            "上游状态": status,
            "准入说明": (
                f"上游贴源表 {upstream_code} 来源系统为{source_system or '空'}，"
                f"与明细表业务域{dwd_system}不一致"
            ),
            "刷新结论": REFRESH_BLOCKED,
            "来源系统": source_system,
            "生效": False,
        }
    return {
        "准入结论": ADMITTED,
        "上游状态": status,
        "准入说明": f"上游贴源表 {upstream_code} 已上线且来源系统一致，允许刷新",
        "刷新结论": REFRESH_READY,
        "来源系统": source_system,
        "生效": True,
    }


def build_record(
    *,
    ods_row: dict[str, Any],
    dwd_row: dict[str, Any],
    upstream_code: str,
    verdict: dict[str, Any],
    commit_seq: int,
    effective_seq: int | None,
) -> dict[str, Any]:
    return {
        "dwd_id": int(dwd_row.get("id", 0)),
        "dwd_code": str(dwd_row.get("表编号", "")),
        "dwd_system": normalize_system(dwd_row.get("业务域")),
        "上游贴源表": upstream_code,
        "上游贴源表ID": int(ods_row.get("id", 0)),
        "上游来源系统": normalize_system(ods_row.get("来源系统")),
        "准入结论": verdict["准入结论"],
        "准入说明": verdict["准入说明"],
        "上游状态": verdict["上游状态"],
        "刷新结论": verdict["刷新结论"],
        "已提交": True,
        "提交序号": commit_seq,
        # 先提交的完整记录决定生效序号；后续重复登记不会改写它。
        "生效序号": effective_seq if effective_seq is not None else commit_seq,
    }


def upsert_record(*, dwd_row: dict[str, Any], ods_row: dict[str, Any], upstream_code: str) -> dict[str, Any]:
    verdict = evaluate(ods_row, dwd_row, upstream_code)
    existing = relation_for(int(dwd_row.get("id", 0)))
    same_relation = (
        existing is not None
        and normalize_table_code(existing.get("上游贴源表")) == upstream_code
    )
    if same_relation:
        commit_seq = int(existing.get("提交序号") or store.next_commit_seq())
        effective_seq = existing.get("生效序号")
    else:
        # 上游换成另一张表是一次新的完整提交，按提交序号重新仲裁。
        commit_seq = store.next_commit_seq()
        effective_seq = None
    record = build_record(
        ods_row=ods_row,
        dwd_row=dwd_row,
        upstream_code=upstream_code,
        verdict=verdict,
        commit_seq=commit_seq,
        effective_seq=effective_seq,
    )
    # 只有先提交的完整记录才生效；后续并发提交只保留落库顺序，不覆盖已生效结论。
    record["生效"] = bool(verdict["生效"]) and record["生效序号"] == commit_seq
    if existing is not None:
        existing.clear()
        existing.update(record)
        return existing
    store.relation_rows().append(record)
    return record


def sync_ods_downstream(ods_row: dict[str, Any]) -> list[dict[str, Any]]:
    """贴源表状态变化后回写所有引用它的明细上游结论；明细定义本身保留。"""
    code = normalize_table_code(ods_row.get("表编号"))
    affected: list[dict[str, Any]] = []
    for relation in relation_rows():
        if normalize_table_code(relation.get("上游贴源表")) != code:
            continue
        dwd_row = next(
            (row for row in store.rows(DWD_MODULE) if int(row.get("id", 0)) == int(relation.get("dwd_id", 0))),
            None,
        )
        if dwd_row is None:
            continue
        verdict = evaluate(ods_row, dwd_row, normalize_table_code(relation.get("上游贴源表")))
        relation["准入结论"] = verdict["准入结论"]
        relation["准入说明"] = verdict["准入说明"]
        relation["上游状态"] = verdict["上游状态"]
        relation["刷新结论"] = verdict["刷新结论"]
        relation["上游来源系统"] = normalize_system(ods_row.get("来源系统"))
        relation["生效"] = bool(verdict["生效"]) and bool(relation.get("已提交"))
        affected.append(relation)
    return affected


def annotate_dwd_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """在明细列表里补上准入结论，让明细详情与贴源列表显示同一口径。

    返回副本：准入结论是读时派生值，不写回明细定义，界面临时值不会污染已提交关系。
    """
    annotated: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        relation = relation_for(int(row.get("id", 0)))
        if relation is not None:
            effective = bool(relation.get("生效"))
            row["上游准入结论"] = relation.get("准入结论")
            row["上游准入说明"] = relation.get("准入说明")
            row["刷新结论"] = relation.get("刷新结论")
            row["上游贴源表状态"] = relation.get("上游状态")
            row["上游准入生效序号"] = relation.get("生效序号") if effective else None
            row["上游准入生效"] = effective
            annotated.append(row)
            continue
        # 历史明细定义只保留了上游表名，没有已提交的准入记录：
        # 不能据此放行刷新，只读推断并提示重新登记。
        code = normalize_table_code(row.get("上游贴源表"))
        ods_row = find_ods(code)
        verdict = evaluate(ods_row, row, code)
        row["上游准入结论"] = BLOCKED
        row["上游准入说明"] = (
            "历史明细定义缺少已提交的上游准入记录，请重新登记上游"
            if code
            else "尚未登记上游贴源表"
        )
        row["刷新结论"] = REFRESH_BLOCKED
        row["上游贴源表状态"] = verdict["上游状态"]
        row["上游准入生效"] = False
        annotated.append(row)
    return annotated


def downstream_for(code: str) -> list[dict[str, Any]]:
    """汇总引用某张贴源表的明细：已提交关系优先，历史定义按未登记准入列出。"""
    normalized = normalize_table_code(code)
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for relation in relation_rows():
        if normalize_table_code(relation.get("上游贴源表")) != normalized:
            continue
        seen.add(int(relation.get("dwd_id", 0)))
        result.append(dict(relation))
    for row in store.rows(DWD_MODULE):
        dwd_id = int(row.get("id", 0))
        if dwd_id in seen:
            continue
        if normalize_table_code(row.get("上游贴源表")) != normalized:
            continue
        result.append({
            "dwd_id": dwd_id,
            "dwd_code": str(row.get("表编号", "")),
            "dwd_system": normalize_system(row.get("业务域")),
            "上游贴源表": normalized,
            "准入结论": BLOCKED,
            "准入说明": "历史明细定义缺少已提交的上游准入记录，请重新登记上游",
            "上游状态": str((find_ods(normalized) or {}).get("status", "")),
            "刷新结论": REFRESH_BLOCKED,
            "已提交": False,
            "生效": False,
        })
    return result


def annotate_ods_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """在贴源列表里补上下游明细数量与受影响结论；同样返回副本。"""
    annotated: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        downstream = downstream_for(str(row.get("表编号", "")))
        row["下游明细数"] = len(downstream)
        row["受影响明细数"] = sum(
            1 for relation in downstream if relation.get("准入结论") != ADMITTED
        )
        status = str(row.get("status", ""))
        if status == "已上线":
            row["准入结论"] = ADMITTED
        elif status == "已下线":
            row["准入结论"] = INACTIVE
        else:
            row["准入结论"] = BLOCKED
        annotated.append(row)
    return annotated


def record_audit(
    *,
    operation_table: str,
    change_type: str,
    before_value: str,
    after_value: str,
    reason: str,
    operator: str = "值班管理员",
    occurred_at: str | None = None,
) -> dict[str, Any]:
    """把一次准入/流转结论写入变更审计；调用方负责与业务写入同事务提交。"""
    from datetime import datetime

    rows = store.rows("auditlog")
    entry: dict[str, Any] = {
        "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
        "status": "已记录",
        "pending": False,
        "abnormal": False,
        "记录编号": _next_audit_code(rows),
        "操作表名": operation_table,
        "变更类型": change_type,
        "变更前值": before_value,
        "变更后值": after_value,
        "操作人": operator,
        "操作时间": occurred_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "变更原因": reason,
    }
    rows.append(entry)
    return entry


def _next_audit_code(rows: list[dict[str, Any]]) -> str:
    sequence = max((int(row.get("id", 0)) for row in rows), default=0) + 1
    return f"AUDI-{sequence:04d}"


def upstream_state_label(relation: dict[str, Any] | None) -> str:
    if relation is None:
        return "上游贴源表：未登记｜准入结论：准入失败"
    return (
        f"上游贴源表：{relation.get('上游贴源表')}（{relation.get('上游状态')}）"
        f"｜准入结论：{relation.get('准入结论')}"
        f"｜刷新：{relation.get('刷新结论')}"
    )
