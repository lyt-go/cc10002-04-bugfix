"""明细层接口：维护明细表，覆盖提交建表审批、确认上线、下线表等动作。

上游贴源表的登记/变更走独立的准入接口：只有目标贴源表已上线且来源系统一致才落库，
贴源表下线后这里返回同一份准入结论，用于跨入口核对。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.dwd import DwdService

router = APIRouter(prefix="/api/dwd", tags=["明细层"])

service = DwdService()

LIST_FIELDS = ["表编号", "表名称", "业务域", "主键列", "度量列", "时间分区列", "上游贴源表", "刷新水位"]
STATUSES = ["草稿", "建表审批中", "已上线", "已下线"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按表编号检索"),
    status: str | None = Query(default=None, description="草稿、建表审批中、已上线、已下线"),
    upstream: str | None = Query(default=None, description="按上游贴源表编号检索，用于跨入口联动"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按表编号、状态与上游贴源表过滤明细层列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword, status=status, upstream=upstream, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条明细表明细，并带上与贴源列表一致的上游准入结论。"""
    entry, relation = service.detail(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"明细表 {entry_id} 不存在或已归档")
    return {"entry": entry, "upstream": relation}


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条明细表；带上游贴源表时先做准入校验，失败不落库。"""
    entry, missing, message = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message="明细表已登记", entry=entry)


@router.post("/{entry_id}/upstream", response_model=ActionResult)
def save_upstream(entry_id: int, payload: EntryPayload) -> ActionResult:
    """登记或变更明细表的上游贴源表；准入失败时说明原因且不落库。"""
    entry, message = service.save_upstream(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条明细表执行提交建表审批、确认上线、下线表；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出明细层清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "dwd", "total": total, "items": items}
