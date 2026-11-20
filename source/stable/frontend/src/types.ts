export type Json = null | boolean | number | string | Json[] | { [key: string]: Json }
export interface Ref {
  id: string
  version: string
}
export interface Agent {
  id: string
  name: string
  version: string
  role: string
  responsibilities: string
  skills: string[]
  knowledge_base_ids?: string[]
  provider: string
  model: string
  tools: Ref[]
  enabled: boolean
  available: boolean
  max_concurrency: number
  task_types: string[]
  estimated_cost: number | null
  estimated_latency_ms: number | null
  response_format?: 'text' | 'prompt_json' | 'json_object'
}
export type NodeKind = 'task' | 'parallel' | 'condition' | 'loop' | 'subworkflow'
export interface WorkflowNode {
  id: string
  kind: NodeKind
  config: Record<string, Json>
}
export interface WorkflowEdge {
  source: string
  target: string
  branch?: string | null
}
export interface Workflow {
  id: string
  name: string
  version: string
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  scheduling_strategy: 'fixed' | 'round_robin' | 'caas'
  max_concurrency: number
  timeout_seconds: number
  max_node_executions: number
  max_subworkflow_depth: number
  error_policy: 'continue' | 'fail_fast'
  input_schema: Record<string, Json>
  output_schema: Record<string, Json>
  output_mapping: Record<string, Json>
  resource_limits: Record<string, number>
}
export interface RunSummary {
  id: string
  workflow: Ref
  state: string
  elapsed_ms: number
  started_at: string | null
  ended_at: string | null
  simulated: boolean | null
  verified: boolean
  error_code: string | null
  nodes: number
  failed_nodes: number
}
export interface NodeRun {
  model_calls?: Json[]
  id: string
  node_id: string
  state: string
  input: Json
  output: Json
  elapsed_ms: number
  agent_ref: Ref | null
  execution_id: string | null
  error_code: string | null
  simulated: boolean | null
  verified: boolean
  attempt_ids: string[]
  verification_ids: string[]
  feedback_ids: string[]
  child_run_ids: string[]
  started_at: string | null
  ended_at: string | null
  waiting_ms: number | null
  scheduling_decision_id: string | null
  provider?: string | null
  model?: string | null
  resolved_model?: string | null
  input_tokens?: number
  output_tokens?: number
  usage_complete?: boolean | null
  estimated_cost?: number | null
  cost_currency?: string | null
  cost_complete?: boolean
}
export interface RunReport {
  replay?: { read_only: boolean; sanitized: boolean; runtime_build_version: string | null }
  run: {
    id: string
    definition_ref: Ref
    state: string
    parent_run_id: string | null
    revision: number
  }
  root_run_id: string
  input: Json
  output: Json
  nodes: NodeRun[]
  elapsed_ms: number
  started_at: string | null
  ended_at: string | null
  simulated: boolean | null
  verified: boolean
  error_code: string | null
  resource_usage: Record<string, number>
  events: {
    id: string
    type: string
    sequence: number
    occurred_at: string
    payload: Record<string, Json>
  }[]
}
export interface DemoInfo {
  mode: 'historical_read_only'
  real_calls_enabled: false
  runtime_build_version: null
  redactions: Record<string, number>
  runs: {
    run_id: string
    state: string
    simulated: boolean | null
    verified: boolean
    elapsed_ms: number
    started_at: string
    execution_order: string[]
    resource_usage: Record<string, number>
    data_mappings: { from: string; to: string; input_field: string; matches: boolean }[]
    nodes?: {
      node_id: string
      state: string
      attempts: {
        id: string
        ordinal: number
        verdict: string
        model: string
        resolved_model: string | null
        input_tokens: number
        output_tokens: number
      }[]
      feedback_bindings: { attempt_id: string; confirmed: boolean }[]
    }[]
  }[]
  benchmark: {
    groups: {
      scenario: string
      strategy: string
      capacity_violations: number
      metrics: Record<string, { mean: number | null }>
    }[]
    paired_comparisons: { significant_05: boolean }[]
  } | null
}
export interface Overview {
  scope: string
  sample_size: number
  agents: { total: number; enabled: number; available: number }
  workflows: { total: number; versions: number }
  runs: { total: number; terminal: number; succeeded: number; active: number }
  verification: Record<string, number>
  strategies: Record<string, number>
  recent: RunSummary[]
  exceptions: RunSummary[]
}
export interface Policy {
  ref: Ref
  name: string
  timeout_seconds: number
  checks: { kind: string; config: Record<string, Json> }[]
}
export interface Profile {
  agent_ref: Ref
  skills: string[]
  task_types: string[]
  tools: Ref[]
  profile_version: string
  capacity: { active: number; capacity: number; peak_active: number }
  confidence: number
  low_samples: boolean
  estimated_reliability: number
  stats: {
    sample_count: number
    successes: number
    failures: number
    empirical_success_rate: number | null
    mean_duration_ms: number | null
  }
}
export interface Decision {
  id: string
  task_id: string
  node_id: string
  strategy: string
  selected: Ref | null
  outcome: string
  effective_priority: number
  elapsed_ms: number
  waiting_ms: number
  created_at: string
  candidates: {
    agent_id: string
    agent_version: string | null
    eligible: boolean
    reasons: string[]
    components: Record<string, number>
    score: number | null
    profile: { profile_version: string } | null
  }[]
}
export interface Attempt {
  retrieval_records?: Json[]
  id: string
  ordinal: number
  state: string
  input_ref: Ref
  input_digest: string
  input_value: Json
  agent_ref: Ref | null
  provider: string | null
  model: string | null
  decision_id: string | null
  execution_id: string | null
  simulated: boolean | null
  error_code: string | null
  started_at: string | null
  ended_at: string | null
  resources: Record<string, number>
  model_calls?: Json[]
  input_tokens?: number
  output_tokens?: number
  usage_complete?: boolean | null
  resolved_model?: string | null
  estimated_cost?: number | null
  cost_currency?: string | null
  provider_config_ref?: Ref | null
  artifact: {
    ref: Ref
    digest: string
    content: Json
    simulated: boolean | null
    execution_error: string | null
  } | null
}
export interface Verification {
  record: { id: string; verdict: string; reason_codes: string[]; verifier_ref: Ref }
  attempt_id: string
  status: string
  artifact_ref: Ref
  artifact_digest: string
  policy_digest: string
  origin: string
  elapsed_ms: number
  evidence: {
    kind: string
    status: string
    reason: string
    path: string | null
    reference: string | null
    rule?: string | null
    schema_path?: string | null
    details?: Json | null
  }[]
}
export interface Feedback {
  record: { id: string; action: string; reason: string; created_at?: string }
  attempt_id: string
  category: string
  origin: string
  repair_action: string
  actual_budget_cost: Record<string, number>
  evidence_refs: string[]
}
export interface Ticket {
  id: string
  node_id: string
  attempt_id: string
  artifact_ref: Ref
  state: string
  revision: number
  actor: string | null
}

export interface ModelPolicy {
  id: string
  supports_tools: boolean
  response_formats: string[]
  max_context_bytes: number
  max_output_tokens: number
  input_price_per_million: number | null
  output_price_per_million: number | null
  currency: string | null
}
export interface ModelProvider {
  id: string
  kind: string
  mode: string
  enabled: boolean
  configured: boolean
  base_url: string | null
  key_env?: string
  credentials_configured: boolean
  models: ModelPolicy[]
  timeout_seconds?: number
  retry?: { max_retries: number; initial_delay: number; max_delay: number }
  connection: { state: string; error_code: string | null; at: string | null }
  revision?: string
  capabilities_source?: string
}
export interface ModelCatalog {
  real_calls_enabled: boolean
  providers: ModelProvider[]
  network_probe_performed: boolean
}
export interface CostEstimate {
  estimated_cost: number | null
  currency: string | null
  basis: string
  exact_cost_hard_budget: boolean
}
