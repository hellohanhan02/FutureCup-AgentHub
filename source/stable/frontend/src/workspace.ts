import { request } from './api'
import type { Json, RunReport } from './types'
export interface PlanStep {
  id: string
  title: string
  role: string
  responsibilities: string
  skills: string[]
  depends_on: string[]
  input_from: string[]
  tools: never[]
  verification: string
  deliverable: string
}
export interface TaskPlan {
  name: string
  goal: string
  expected_delivery: string
  steps: PlanStep[]
}
export interface WorkspaceTask {
  id: string
  created_at: string
  scenario: string
  name?: string
  state: string
  simulated: boolean | null
  mode: 'mock' | 'real'
  planning: 'dynamic' | 'template'
  messages: string[]
  data: Record<string, Json>
  revision: number
  plan: TaskPlan | null
  run_id: string | null
  usage: Record<string, number>
  estimated_cny_reserved: number
  budget_cny: number
  budget_preview?: BudgetQuote
  max_calls: number
  output_profile?: 'concise' | 'design_document'
  error_code: string | null
  knowledge_warning?: string
  knowledge_enhanced?: boolean
  knowledge_sources?: { id: string; document_name: string; locator: string; text: string }[]
  error_phase?: 'planning' | 'execution' | null
  error_details?: { path: string; rule: string; message: string }[]
  planning_attempts?: unknown[]
  planning_verifications?: unknown[]
  planning_feedback?: unknown[]
  report?: RunReport
  events: { type: string; at: string }[]
  delivery?: {
    title: string
    summary: string
    items: string[]
    citations?: { chunk_id: string; quote: string }[]
    markdown?: string
    order?: string[]
    evidence?: { quote: string; comment: string }[]
  } | null
}
export interface WorkspaceCapabilities {
  real_available: boolean
  models: { provider: string; models: string[] }[]
}
export interface BudgetQuote {
  estimated_admission_upper_cny: number | null
  budget_cny: number
  server_budget_cny: number
  admitted: boolean
}
const prefix = '/workspace'
const post = <T>(path: string, body: unknown) =>
  request<T>(prefix + path, { method: 'POST', body: JSON.stringify(body) })
export const workspaceApi = {
  budgetPreview: (body: unknown) => post<BudgetQuote>('/budget-preview', body),
  capabilities: () => request<WorkspaceCapabilities>(prefix + '/capabilities'),
  history: () => request<WorkspaceTask[]>(prefix + '/tasks'),
  task: (id: string) => request<WorkspaceTask>(prefix + '/tasks/' + encodeURIComponent(id)),
  create: (body: unknown) => post<WorkspaceTask>('/tasks', body),
  revise: (id: string, body: unknown) =>
    post<WorkspaceTask>('/tasks/' + id + '/requirements', body),
  edit: (id: string, body: unknown) =>
    request<WorkspaceTask>(prefix + '/tasks/' + id + '/plan', {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  confirm: (id: string, body: unknown) => post<WorkspaceTask>('/tasks/' + id + '/confirm', body),
  cancel: (id: string) => post<WorkspaceTask>('/tasks/' + id + '/cancel', {}),
}
export function deliveryMarkdown(task: WorkspaceTask): string {
  if (!task.delivery || task.report?.verified !== true) return ''
  const d = task.delivery
  return [
    `# ${d.title}`,
    '',
    d.summary,
    '',
    ...(d.markdown ? ['', d.markdown] : d.items.map((x) => '- ' + x)),
    ...(d.order ? ['', '## 调度顺序', ...d.order.map((x, i) => `${i + 1}. ${x}`)] : []),
    ...(d.evidence
      ? ['', '## 原文引用与审查', ...d.evidence.flatMap((e) => ['', '> ' + e.quote, e.comment])]
      : []),
    ...(d.citations
      ? ['', '## 知识来源引用', ...d.citations.flatMap((c) => ['- ' + c.chunk_id, '> ' + c.quote])]
      : []),
    '',
    `模式：${task.simulated === true ? 'MOCK 模拟' : task.simulated === false ? '真实模型记录' : '未知'}；仅通过配置规则，非普遍正确性证明。`,
  ].join('\n')
}
