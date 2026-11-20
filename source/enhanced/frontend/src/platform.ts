import type { RunReport, Decision, Attempt, Verification, Feedback } from './types'
export interface LedgerView {
  available: boolean; reason?: string; account_id?: string; unit?: string
  max_calls?: number; max_units?: number; occupied_calls?: number; confirmed_calls?: number
  unknown_calls?: number; reserved_calls?: number; confirmed_units?: number; reserved_units?: number
  unknown_units?: number; remaining_calls?: number; remaining_units?: number
  tools?: Record<string, unknown>[]; findings?: Record<string, unknown>[]; truncated?: boolean
}
export interface Observation {
  schema_version: string; run_id: string; revision: number; report: RunReport
  graph: { nodes: { id: string; kind: string }[]; edges: { source: string; target: string; branch?: string | null }[] }
  node_configs: Record<string, Record<string, unknown>>
  audits: Record<string, { attempts: Attempt[]; verifications: Verification[]; feedback: Feedback[] }>
  decisions: Decision[]; ledger: LedgerView
  links: { source_run_id: string | null; recovery_run_id: string | null }
  recovery: { requestable: boolean; reasons: string[]; expected_revision: number; existing_run_id: string | null }
  redactions: Record<string, number>
}
export interface EvidenceExport { run_id: string; revision: number; evidence: Observation; sha256: string }
export function validateExport(value: EvidenceExport, id: string, revision: number) {
  if (value.run_id !== id || value.evidence?.run_id !== id || value.evidence?.report?.run.id !== id || value.revision !== revision || value.evidence.revision !== revision)
    throw new Error('报告与当前任务或版本不一致，请刷新后重新导出')
  return value
}
export function layoutGraph(graph: Observation['graph']) {
  const levels = new Map(graph.nodes.map(n => [n.id, 0]))
  for (let i = 0; i < graph.nodes.length; i++)
    for (const e of graph.edges)
      if (levels.has(e.source) && levels.has(e.target)) levels.set(e.target, Math.max(levels.get(e.target)!, levels.get(e.source)! + 1))
  const slots = new Map<number, number>()
  return graph.nodes.map(n => {
    const level = levels.get(n.id)!, slot = slots.get(level) ?? 0
    slots.set(level, slot + 1)
    return { ...n, x: 18 + level * 230, y: 24 + slot * 112 }
  })
}
export const recoveryReason: Record<string, string> = {
  recovery_child_requires_root: '请在根工作流查看恢复，子运行不单独认领预算',
  recovery_source_not_failed: '仅终态失败运行可申请恢复',
  recovery_real_budget_unprovable: '真实或未知模型的跨运行预算尚不可证明，恢复已关闭',
  recovery_deadline_exhausted: '原工作流整体期限已耗尽', recovery_generation_limit: '已达到一次跨运行恢复限制',
  recovery_source_claimed: '该源运行已有恢复认领', recovery_ledger_missing: '缺少原持久预算账户',
  recovery_effect_authorization: '工具缺少安全重放证明，需要外部对账；此界面不提供强制重放',
  ledger_budget_snapshot_mismatch: '执行快照与账本不一致，请人工核对', report_revision_changed: '运行状态已更新，请刷新再导出',
}
