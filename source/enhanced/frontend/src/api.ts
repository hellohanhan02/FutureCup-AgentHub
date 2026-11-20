import type {
  ModelCatalog,
  CostEstimate,
  Agent,
  Workflow,
  RunSummary,
  RunReport,
  Overview,
  Policy,
  Profile,
  Decision,
  Attempt,
  Verification,
  Feedback,
  Ticket,
} from './types'

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message)
  }
}
const errors: Record<string, string> = {
  invalid_request: '请求字段有误，请检查必填项、版本号和数值范围',
  agent_exists: 'Agent ID 已存在',
  agent_not_found: 'Agent 或其配置版本不存在',
  version_conflict: 'Agent 配置已被更新，请刷新后重试',
  version_exists: '配置版本已存在，请填写新版本号',
  agent_disabled: 'Agent 已禁用',
  workflow_cycle: '普通依赖图存在环。请使用受限循环节点',
  workflow_mapping_reference: '输入/输出映射引用无效，请检查上游依赖与节点 ID',
  workflow_branch_invalid: '条件分支标签不合法，请连接 true / false 路径',
  workflow_config_invalid: '节点包含不支持的配置字段',
  verification_policy_invalid: '验证规则不合法，请检查 Schema、规则类型和安全限制',
  safety_record_not_found: '所引用的验证规则或审计记录不存在',
  approval_denied: '人工授权失败，请检查服务端配置和令牌',
  stale_intervention: '人工请求已过期，请刷新当前状态',
  real_provider_disabled: '真实模型未获服务端授权',
  provider_credentials_missing: '服务端未配置模型凭据',
}
export async function request<T>(
  path: string,
  init: RequestInit = {},
  timeout = 15000,
): Promise<T> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeout)
  try {
    const response = await fetch('/api/v1' + path, {
      ...init,
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json', ...init.headers },
    })
    const body = await response.json().catch(() => null)
    if (!response.ok) {
      const code = body?.error?.code ?? 'http_error'
      throw new ApiError(
        response.status,
        code,
        `${errors[code] ?? body?.error?.message ?? `HTTP ${response.status}`}（${code}）`,
      )
    }
    return body as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (controller.signal.aborted)
      throw new Error('请求超时，请检查后端状态。提交超时后请先查询运行记录，避免重复执行。')
    throw new Error('无法连接 API，请检查后端服务和 Vite 代理。')
  } finally {
    window.clearTimeout(timer)
  }
}
const post = <T>(path: string, value: unknown) =>
  request<T>(path, { method: 'POST', body: JSON.stringify(value) })
export const api = {
  demoInfo: () =>
    request<import('./types').DemoInfo | { mode: 'interactive'; notice: string }>('/demo-info'),
  demoStructure: (id: string) =>
    request<import('./types').Json>('/demo-structure/' + encodeURIComponent(id)),
  modelCatalog: () => request<ModelCatalog>('/model-providers'),
  estimate: (
    provider: string,
    model: string,
    input_tokens_hint: number,
    max_output_tokens: number,
    calls: number,
  ) =>
    post<CostEstimate>(`/model-providers/${encodeURIComponent(provider)}/estimate`, {
      model,
      input_tokens_hint,
      max_output_tokens,
      calls,
    }),
  observation: (id: string) => request<import('./platform').Observation>('/workflow-runs/'+encodeURIComponent(id)+'/observability'),
  exportReport: (id: string, revision: number) => request<import('./platform').EvidenceExport>('/workflow-runs/'+encodeURIComponent(id)+'/report-export?expected_revision='+revision),
  recover: (id: string, revision: number, requestId: string) => post<{run: {id:string}}>('/workflow-runs/'+encodeURIComponent(id)+'/recover', {request_id:requestId,expected_revision:revision,authorize_recovery:true,authorize_effects:false,invalidated_nodes:[]}),
  health: () => request<{ status: string; scope: string }>('/health'),
  overview: () => request<Overview>('/console/overview'),
  agents: () => request<Agent[]>('/agents'),
  agent: (id: string) => request<Agent>('/agents/' + encodeURIComponent(id)),
  createAgent: (value: unknown) => post<Agent>('/agents', value),
  editAgent: (id: string, value: unknown) =>
    request<Agent>('/agents/' + encodeURIComponent(id), {
      method: 'PATCH',
      body: JSON.stringify(value),
    }),
  disableAgent: (id: string) => post<Agent>('/agents/' + encodeURIComponent(id) + '/disable', {}),
  profile: (id: string) => request<Profile>('/scheduling/agents/' + encodeURIComponent(id)),
  workflows: () => request<Workflow[]>('/workflows'),
  workflow: (id: string, version: string) =>
    request<Workflow>(
      `/workflows/${encodeURIComponent(id)}?version=${encodeURIComponent(version)}`,
    ),
  saveWorkflow: (value: Workflow) => post<Workflow>('/workflows', value),
  validateWorkflow: (value: Workflow) => post<{ valid: boolean }>('/workflows/validate', value),
  execute: (id: string, version: string, input: unknown) =>
    post<RunReport>(`/workflows/${encodeURIComponent(id)}/executions`, { version, input }),
  runs: () => request<RunSummary[]>('/workflow-runs?limit=100'),
  run: (id: string) => request<RunReport>('/workflow-runs/' + encodeURIComponent(id)),
  cancel: (id: string) =>
    post<RunReport>('/workflow-runs/' + encodeURIComponent(id) + '/cancel', {}),
  decisions: (id: string) =>
    request<Decision[]>(`/scheduling/decisions?run_id=${encodeURIComponent(id)}&limit=1000`),
  policies: () => request<Policy[]>('/verification-policies'),
  createPolicy: (value: unknown) => post<Policy>('/verification-policies', value),
  attempts: (id: string) => request<Attempt[]>(`/self-loops/${encodeURIComponent(id)}/attempts`),
  verifications: (id: string) =>
    request<Verification[]>(`/self-loops/${encodeURIComponent(id)}/verifications`),
  feedback: (id: string) => request<Feedback[]>(`/self-loops/${encodeURIComponent(id)}/feedback`),
  tickets: () => request<Ticket[]>('/human-interventions'),
  resolve: (ticket: Ticket, decision: 'retry' | 'abort', actor: string, token: string) =>
    request<Ticket>(`/human-interventions/${ticket.id}/resolve`, {
      method: 'POST',
      headers: { 'X-Harness-Approval-Token': token },
      body: JSON.stringify({
        revision: ticket.revision,
        attempt_id: ticket.attempt_id,
        artifact_ref: ticket.artifact_ref,
        decision,
        actor,
      }),
    }),
}
