import { api } from './api'
import type { Workflow } from './types'
import sequential from './examples/sequential.json'
import parallel from './examples/parallel.json'
import selfloop from './examples/selfloop.json'
export type DemoKey = 'sequential' | 'parallel' | 'selfloop'
export const demos: { key: DemoKey; name: string; description: string }[] = [
  {
    key: 'sequential',
    name: '多 Agent 顺序协作',
    description: '研究 → 撰写 → 审核；下游接收上游产物。',
  },
  {
    key: 'parallel',
    name: '并行分析与汇合',
    description: '两个 Agent 并发分析，等待汇合后统一审核。',
  },
  {
    key: 'selfloop',
    name: '验证与反馈修复',
    description: '脚本化 Mock 首次结构失败，收到反馈后第二次通过。',
  },
]
export function blankWorkflow(): Workflow {
  return {
    id: 'workflow-' + Date.now(),
    name: '未命名工作流',
    version: '1',
    nodes: [],
    edges: [],
    scheduling_strategy: 'caas',
    max_concurrency: 4,
    timeout_seconds: 60,
    max_node_executions: 100,
    max_subworkflow_depth: 4,
    error_policy: 'continue',
    input_schema: {},
    output_schema: {},
    output_mapping: {},
    resource_limits: {},
  }
}
const templates = { sequential, parallel, selfloop }
export function demoWorkflow(key: DemoKey): Workflow {
  return { ...blankWorkflow(), ...JSON.parse(JSON.stringify(templates[key])) } as Workflow
}
export function canonical(value: unknown): string {
  return JSON.stringify(value, (_, v) =>
    v && typeof v === 'object' && !Array.isArray(v)
      ? Object.fromEntries(Object.entries(v).sort(([a], [b]) => a.localeCompare(b)))
      : v,
  )
}
export async function initializeDemos(): Promise<void> {
  const existing = await api.agents()
  for (const [id, name, role, model] of [
    ['fc-researcher', '研究分析师', 'research', 'mock-echo'],
    ['fc-writer', '内容撰写师', 'writing', 'mock-echo'],
    ['fc-reviewer', '结果审核师', 'review', 'mock-echo'],
    ['fc-repair', '反馈修复演示', 'repair', 'mock-repair-demo'],
  ]) {
    const found = existing.find((a) => a.id === id)
    if (found) {
      if (
        found.provider !== 'mock' ||
        found.model !== model ||
        !found.enabled ||
        !found.available ||
        found.version !== '1'
      )
        throw new Error(
          `示例 Agent ${id} 已被修改或禁用。请恢复示例配置或使用其他 ID；初始化不会覆盖。`,
        )
      continue
    }
    await api.createAgent({
      id,
      name,
      role,
      model,
      provider: 'mock',
      version: '1',
      responsibilities: '明确标记的 Mock 示例：' + role,
      system_prompt: 'This is a MOCK SIMULATED demonstration, not real LLM reasoning.',
      skills: ['analysis', role],
      tools: [],
      task_types: ['*'],
    })
  }
  const checks = [
    {
      kind: 'json_schema',
      config: {
        schema: {
          type: 'object',
          required: ['answer'],
          properties: { answer: { type: 'integer', const: 42 } },
          additionalProperties: false,
        },
      },
    },
  ]
  const policy = (await api.policies()).find(
    (p) => p.ref.id === 'fc-demo-answer' && p.ref.version === '1',
  )
  if (policy && canonical(policy.checks) !== canonical(checks))
    throw new Error('示例规则版本已存在且内容不一致，未覆盖。')
  if (!policy)
    await api.createPolicy({
      id: 'fc-demo-answer',
      version: '1',
      name: 'Mock 示例：answer 必须为 42',
      checks,
    })
}
