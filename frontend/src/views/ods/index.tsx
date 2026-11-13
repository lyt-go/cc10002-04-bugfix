import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { request } from '@/api/client'

const ENDPOINT = '/api/ods'
const columns = [
  "表编号",
  "表名称",
  "来源系统",
  "原始字段数",
  "增量字段",
  "全量快照策略",
  "每日增量行",
  "最近加载时间",
  "下游明细数",
  "受影响明细数"
]
const actions = ["提交建表审批", "确认上线", "下线表"]
// 单向推进：每个状态只放行下一步动作；重复动作与跨级动作都会被后端拦下。
const allowedActions: Record<string, string[]> = {
  "草稿": ["提交建表审批"],
  "建表审批中": ["确认上线"],
  "已上线": ["下线表"],
  "已下线": [],
}

type Row = Record<string, string | number | boolean | null>
type Relation = Record<string, string | number | boolean | null>

async function readResult(response: Response): Promise<{ ok: boolean; message: string; entry?: Row }> {
  let payload: { ok?: boolean; message?: string; entry?: Row; detail?: string } = {}
  try {
    payload = await response.json()
  } catch {
    payload = {}
  }
  if (!response.ok) {
    return { ok: false, message: payload.detail ?? `接口返回 ${response.status}，动作未生效` }
  }
  return {
    ok: payload.ok !== false,
    message: payload.message ?? (payload.ok === false ? '动作未生效' : '动作已生效'),
    entry: payload.entry,
  }
}

export default function OdsView() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [detail, setDetail] = useState<{ entry: Row; downstream: Relation[] } | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [form, setForm] = useState({ 表编号: '', 表名称: '', 来源系统: '' })
  const [filters, setFilters] = useState<Record<string, string>>({
    keyword: searchParams.get('keyword') ?? '',
  })

  useEffect(() => {
    const keyword = searchParams.get('keyword') ?? ''
    setFilters((prev) => (prev.keyword === keyword ? prev : { ...prev, keyword }))
  }, [searchParams])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams()
    if (filters.keyword) query.set('keyword', filters.keyword)
    try {
      const response = await request(`${ENDPOINT}?${query.toString()}`)
      if (!response.ok) {
        throw new Error('贴源表列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '贴源层列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  const counts = useMemo(() => ({
    total,
    online: rows.filter((row) => row.status === '已上线').length,
    approving: rows.filter((row) => row.status === '建表审批中').length,
  }), [rows, total])

  async function openDetail(row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}`)
      if (!response.ok) throw new Error('贴源表详情读取失败')
      setDetail(await response.json())
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '贴源表详情读取失败')
    }
  }

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      const result = await readResult(response)
      if (!result.ok) {
        setErrorMessage(result.message)
        return
      }
      setNotice(result.message)
      await reload()
      if (detail && detail.entry.id === row.id) await openDetail(row)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '贴源层操作失败')
    }
  }

  async function submitCreate() {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(ENDPOINT, {
        method: 'POST',
        body: JSON.stringify({ values: form }),
      })
      const result = await readResult(response)
      if (!result.ok) {
        setErrorMessage(result.message)
        return
      }
      setNotice(result.message)
      setForm({ 表编号: '', 表名称: '', 来源系统: '' })
      setShowCreate(false)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '贴源表登记失败')
    }
  }

  return (
    <section className="page" data-module="ods">
      <header className="page-head">
        <div>
          <h2>贴源层管理</h2>
          <p className="page-desc">贴源表按 草稿 → 建表审批中 → 已上线 → 已下线 单向推进；下线会回写引用它的明细准入结论。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setShowCreate((prev) => !prev)}>
            登记贴源表
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出贴源层清单
          </button>
        </div>
      </header>

      {showCreate && (
        <form
          className="inline-form"
          onSubmit={(event) => {
            event.preventDefault()
            void submitCreate()
          }}
        >
          {(["表编号", "表名称", "来源系统"] as const).map((field) => (
            <label key={field} className="filter-item">
              <span>{field}</span>
              <input
                value={form[field]}
                onChange={(event) => setForm((prev) => ({ ...prev, [field]: event.target.value }))}
              />
            </label>
          ))}
          <button className="btn primary" type="submit">提交登记</button>
          <button className="btn ghost" type="button" onClick={() => setShowCreate(false)}>取消</button>
        </form>
      )}

      <div className="stat-row">
        <article className="stat-card"><span className="stat-label">贴源表总数</span><strong className="stat-value">{counts.total}</strong></article>
        <article className="stat-card"><span className="stat-label">已上线表</span><strong className="stat-value">{counts.online}</strong></article>
        <article className="stat-card"><span className="stat-label">建表审批中</span><strong className="stat-value">{counts.approving}</strong></article>
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        <label className="filter-item">
          <span>表编号</span>
          <input
            value={filters.keyword ?? ''}
            placeholder="按表编号检索"
            onChange={(event) => {
              const keyword = event.target.value
              setFilters((prev) => ({ ...prev, keyword }))
              setSearchParams(keyword ? { keyword } : {})
            }}
          />
        </label>
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => { setFilters({}); setSearchParams({}) }}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>准入结论</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td>
                <span className={row.准入结论 === '准入通过' ? 'badge ok' : 'badge warn'}>{String(row.准入结论 ?? '—')}</span>
              </td>
              <td className="row-actions">
                {actions.map((action) => {
                  const enabled = (allowedActions[String(row.status)] ?? []).includes(action)
                  return (
                    <button
                      key={action}
                      className="link"
                      type="button"
                      disabled={!enabled}
                      title={enabled ? action : `当前状态${String(row.status)}不能${action}`}
                      onClick={() => void runAction(action, row)}
                    >
                      {action}
                    </button>
                  )
                })}
                <button className="link" type="button" onClick={() => void openDetail(row)}>详情</button>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 2}>暂无贴源层数据，可先登记贴源表</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条贴源层记录{notice && `　·　${notice}`}</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>

      {detail && (
        <aside className="detail-panel" role="dialog" aria-label="贴源表详情">
          <header className="detail-head">
            <strong>贴源表详情：{String(detail.entry.表编号)}</strong>
            <button className="btn ghost" type="button" onClick={() => setDetail(null)}>关闭</button>
          </header>
          <dl className="detail-list">
            <dt>表名称</dt><dd>{String(detail.entry.表名称 ?? '—')}</dd>
            <dt>来源系统</dt><dd>{String(detail.entry.来源系统 ?? '—')}</dd>
            <dt>当前状态</dt><dd>{String(detail.entry.status ?? '—')}</dd>
            <dt>准入结论</dt>
            <dd><span className={detail.entry.准入结论 === '准入通过' ? 'badge ok' : 'badge warn'}>{String(detail.entry.准入结论 ?? '—')}</span></dd>
            <dt>受影响明细</dt><dd>{String(detail.entry.受影响明细数 ?? 0)}</dd>
          </dl>
          <h3 className="detail-title">引用该贴源表的明细</h3>
          {detail.downstream.length === 0 && <p className="page-desc">暂无明细表引用；可在明细层登记上游时选择本表。</p>}
          {detail.downstream.length > 0 && (
            <table className="data-table">
              <thead>
                <tr><th>明细表</th><th>来源系统</th><th>准入结论</th><th>刷新结论</th><th>操作</th></tr>
              </thead>
              <tbody>
                {detail.downstream.map((relation) => (
                  <tr key={String(relation.dwd_code)}>
                    <td>{String(relation.dwd_code)}</td>
                    <td>{String(relation.dwd_system ?? '—')}</td>
                    <td>{String(relation.准入结论 ?? '—')}</td>
                    <td>{String(relation.刷新结论 ?? '—')}</td>
                    <td>
                      <Link className="link" to={`/dwd?upstream=${encodeURIComponent(String(relation.上游贴源表))}`}>
                        在明细层查看
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </aside>
      )}
    </section>
  )
}
