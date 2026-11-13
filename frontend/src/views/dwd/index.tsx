import { useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { request } from '@/api/client'

const ENDPOINT = '/api/dwd'
const columns = [
  "表编号",
  "表名称",
  "业务域",
  "主键列",
  "度量列",
  "时间分区列",
  "上游贴源表",
  "刷新水位",
  "上游准入结论",
  "刷新结论"
]
const actions = ["提交建表审批", "确认上线", "下线表"]
// 与贴源页一致：只放行下一步动作，跨级/重复动作后端也会拒绝。
const allowedActions: Record<string, string[]> = {
  "草稿": ["提交建表审批"],
  "建表审批中": ["确认上线"],
  "已上线": ["下线表"],
  "已下线": [],
}

type Row = Record<string, string | number | boolean | null>
type Upstream = Record<string, string | number | boolean | null>

async function readResult(response: Response): Promise<{ ok: boolean; message: string }> {
  let payload: { ok?: boolean; message?: string; detail?: string } = {}
  try {
    payload = await response.json()
  } catch {
    payload = {}
  }
  if (!response.ok) {
    return { ok: false, message: payload.detail ?? `接口返回 ${response.status}，未保存` }
  }
  return { ok: payload.ok !== false, message: payload.message ?? '动作已生效' }
}

export default function DwdView() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [upstreamFilter, setUpstreamFilter] = useState(searchParams.get('upstream') ?? '')
  const [selected, setSelected] = useState<{ entry: Row; upstream: Upstream | null } | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [form, setForm] = useState({ 表编号: '', 表名称: '', 业务域: '', 上游贴源表: '' })
  const [upstreamInput, setUpstreamInput] = useState('')

  useEffect(() => {
    setUpstreamFilter(searchParams.get('upstream') ?? '')
  }, [searchParams])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams()
    if (upstreamFilter) query.set('upstream', upstreamFilter)
    try {
      const response = await request(`${ENDPOINT}?${query.toString()}`)
      if (!response.ok) {
        throw new Error('明细表列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '明细层列表读取失败')
    }
  }, [upstreamFilter])

  useEffect(() => {
    void reload()
  }, [reload])

  async function openDetail(row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}`)
      if (!response.ok) throw new Error('明细表详情读取失败')
      const payload = await response.json()
      setSelected(payload)
      setUpstreamInput(String(payload.upstream?.上游贴源表 ?? row.上游贴源表 ?? ''))
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '明细表详情读取失败')
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
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '明细层操作失败')
    }
  }

  async function submitCreate() {
    setErrorMessage('')
    setNotice('')
    try {
      const values = { ...form }
      const response = await request(ENDPOINT, {
        method: 'POST',
        body: JSON.stringify({ values }),
      })
      const result = await readResult(response)
      if (!result.ok) {
        setErrorMessage(result.message)
        return
      }
      setNotice(result.message)
      setForm({ 表编号: '', 表名称: '', 业务域: '', 上游贴源表: '' })
      setShowCreate(false)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '明细表登记失败')
    }
  }

  async function saveUpstream() {
    if (!selected) return
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${selected.entry.id}/upstream`, {
        method: 'POST',
        body: JSON.stringify({ values: { 上游贴源表: upstreamInput } }),
      })
      const result = await readResult(response)
      if (!result.ok) {
        setErrorMessage(result.message)
        return
      }
      setNotice(result.message)
      await openDetail(selected.entry)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '上游保存失败')
    }
  }

  function clearLink() {
    setUpstreamFilter('')
    setSearchParams({})
  }

  return (
    <section className="page" data-module="dwd">
      <header className="page-head">
        <div>
          <h2>明细层管理</h2>
          <p className="page-desc">明细登记上游前必须确认目标贴源表已上线且来源系统一致；上游下线后保留历史定义但禁止新刷新。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setShowCreate((prev) => !prev)}>
            登记明细表
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出明细层清单
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
          {(["表编号", "表名称", "业务域", "上游贴源表"] as const).map((field) => (
            <label key={field} className="filter-item">
              <span>{field}</span>
              <input
                value={form[field]}
                placeholder={field === '上游贴源表' ? '已上线的贴源表编号' : ''}
                onChange={(event) => setForm((prev) => ({ ...prev, [field]: event.target.value }))}
              />
            </label>
          ))}
          <button className="btn primary" type="submit">提交登记</button>
          <button className="btn ghost" type="button" onClick={() => setShowCreate(false)}>取消</button>
        </form>
      )}

      {upstreamFilter && (
        <div className="link-banner">
          <span>当前按上游贴源表 <strong>{upstreamFilter}</strong> 联动过滤，准入结论与贴源页一致。</span>
          <button className="btn ghost" type="button" onClick={clearLink}>查看全部明细</button>
        </div>
      )}

      <div className="stat-row">
        <article className="stat-card"><span className="stat-label">明细表总数</span><strong className="stat-value">{total}</strong></article>
        <article className="stat-card"><span className="stat-label">已上线表</span><strong className="stat-value">{rows.filter((row) => row.status === '已上线').length}</strong></article>
        <article className="stat-card"><span className="stat-label">准入受影响</span><strong className="stat-value">{rows.filter((row) => row.上游准入结论 !== '准入通过').length}</strong></article>
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        <label className="filter-item">
          <span>上游贴源表</span>
          <input
            value={upstreamFilter}
            placeholder="按上游贴源表编号检索"
            onChange={(event) => setUpstreamFilter(event.target.value)}
          />
        </label>
        <button className="btn" type="submit">查询</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => {
                const value = row[column]
                if (column === '上游准入结论' || column === '刷新结论') {
                  const ok = value === '准入通过' || value === '允许刷新'
                  return (
                    <td key={column}>
                      <span className={ok ? 'badge ok' : 'badge warn'}>{String(value ?? '—')}</span>
                    </td>
                  )
                }
                return <td key={column}>{value ?? '—'}</td>
              })}
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
                <button className="link" type="button" onClick={() => void openDetail(row)}>详情/上游</button>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无明细层数据，可先登记明细表</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条明细层记录{notice && `　·　${notice}`}</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>

      {selected && (
        <aside className="detail-panel" role="dialog" aria-label="明细表详情">
          <header className="detail-head">
            <strong>明细表详情：{String(selected.entry.表编号)}</strong>
            <button className="btn ghost" type="button" onClick={() => setSelected(null)}>关闭</button>
          </header>
          <dl className="detail-list">
            <dt>表名称</dt><dd>{String(selected.entry.表名称 ?? '—')}</dd>
            <dt>业务域（来源系统）</dt><dd>{String(selected.entry.业务域 ?? '—')}</dd>
            <dt>当前状态</dt><dd>{String(selected.entry.status ?? '—')}</dd>
            <dt>当前上游</dt><dd>{String(selected.entry.上游贴源表 ?? '未登记')}</dd>
            <dt>准入结论</dt>
            <dd>
              <span className={selected.entry.上游准入结论 === '准入通过' ? 'badge ok' : 'badge warn'}>
                {String(selected.entry.上游准入结论 ?? '—')}
              </span>
              {' '}{String(selected.entry.上游准入说明 ?? '')}
            </dd>
            <dt>刷新结论</dt><dd>{String(selected.entry.刷新结论 ?? '—')}</dd>
            <dt>上游表状态</dt><dd>{String(selected.entry.上游贴源表状态 ?? '—')}</dd>
          </dl>
          <form
            className="inline-form"
            onSubmit={(event) => {
              event.preventDefault()
              void saveUpstream()
            }}
          >
            <label className="filter-item">
              <span>登记/变更上游贴源表</span>
              <input
                value={upstreamInput}
                placeholder="输入已上线的贴源表编号"
                onChange={(event) => setUpstreamInput(event.target.value)}
              />
            </label>
            <button className="btn primary" type="submit">校验并保存上游</button>
            <Link className="link" to={`/ods?keyword=${encodeURIComponent(upstreamInput || String(selected.entry.上游贴源表 ?? ''))}`}>
              在贴源层查看该表
            </Link>
          </form>
          {selected.upstream && (
            <p className="page-desc">
              已提交记录：提交序号 {String(selected.upstream.提交序号 ?? '—')}，生效序号 {String(selected.upstream.生效序号 ?? '—')}。
            </p>
          )}
        </aside>
      )}
    </section>
  )
}
