import { api } from './api'
import type { Workflow } from './types'
import { blankWorkflow, canonical } from './demos'
import agents from './examples/llm08/agents.json'
import policies from './examples/llm08/policies.json'
import requirements from './examples/llm08/requirements.json'
import data_analysis from './examples/llm08/data_analysis.json'
import selfloop from './examples/llm08/selfloop.json'
import inputs from './examples/llm08/inputs.json'
export const scenarios = { requirements, data_analysis, selfloop }
export type ScenarioName = keyof typeof scenarios
export const scenarioDescriptions = [
  {
    key: 'requirements' as ScenarioName,
    name: 'A · 需求分析与方案审查',
    description: '规划、设计、审查三个角色协作；独立验证交付结构。',
  },
  {
    key: 'data_analysis' as ScenarioName,
    name: 'B · 数据分析协作',
    description: '检查固定数据、分析统计、形成报告；独立核对 3 / 60 / 20。',
  },
  {
    key: 'selfloop' as ScenarioName,
    name: 'C · 有界反馈修复',
    description: '发票计算、Schema 与业务规则验证、失败反馈和有限重试。',
  },
]
export function scenarioWorkflow(name: ScenarioName): Workflow {
  return { ...blankWorkflow(), ...JSON.parse(JSON.stringify(scenarios[name])) } as Workflow
}
export function scenarioInput(name: ScenarioName) {
  return JSON.parse(JSON.stringify(inputs[name]))
}
export async function initializeScenarioMocks() {
  const existing = await api.agents()
  for (const value of agents) {
    const current = existing.find((a) => a.id === value.id)
    if (current) {
      if (
        current.provider !== 'mock' ||
        current.model !== 'mock-scenario08' ||
        current.version !== 'mock-1' ||
        !current.enabled ||
        !current.available
      )
        throw new Error(`Agent ${value.id} 已改为其他配置；Mock 初始化不会覆盖或使用真实模型。`)
    } else await api.createAgent(value)
  }
  const rules = await api.policies()
  for (const value of policies) {
    const found = rules.find((p) => p.ref.id === value.id && p.ref.version === value.version)
    if (found) {
      if (canonical(found.checks) !== canonical(value.checks))
        throw new Error('独立验证规则版本冲突，不会覆盖')
    } else await api.createPolicy(value)
  }
}
