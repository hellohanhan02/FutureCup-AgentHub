<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import {
  workspaceApi,
  deliveryMarkdown,
  type TaskPlan,
  type WorkspaceTask,
  type WorkspaceCapabilities,
  type BudgetQuote,
} from '../workspace'
import { usePolling } from '../composables'
import StatusBadge from '../components/StatusBadge.vue'
import type { Json } from '../types'
import MarkdownDocument from '../components/MarkdownDocument.vue'
import { request as knowledgeRequest } from '../api'
const knowledgeBases = ref<{ id: string; name: string; version: number; document_count: number }[]>(
    [],
  ),
  knowledgeIds = ref<string[]>([])
onMounted(async () => {
  try {
    const value = await knowledgeRequest<unknown>('/knowledge-bases')
    if (!Array.isArray(value)) throw new Error('?????????')
    knowledgeBases.value = value
  } catch {
    /* read-only service has no knowledge API */
  }
})
const outputProfile = ref<'concise' | 'design_document'>('concise')
const sourceContract = ref<'staged-rules-v1' | 'staged-rules-v2'>('staged-rules-v1')
const router = useRouter(),
  route = useRoute()
const task = ref<WorkspaceTask>(),
  history = ref<WorkspaceTask[]>([]),
  cap = ref<WorkspaceCapabilities>(),
  draft = ref<TaskPlan>()
const goal = ref(''),
  scenario = ref('architecture'),
  documentText = ref(''),
  taskIds = ref('A,B,C,D'),
  dependencies = ref('A>C\nB>C\nC>D'),
  windows = ref('')
const planning = ref<'dynamic' | 'template'>('dynamic'),
  mode = ref<'mock' | 'real'>('mock'),
  provider = ref('deepseek'),
  model = ref('deepseek-flash'),
  budget = ref(0.5)
const budgetChoice = ref('0.5'),
  quote = ref<BudgetQuote>(),
  quoteError = ref(''),
  quotePending = ref(false)
watch(budgetChoice, (value) => {
  if (value !== 'custom') budget.value = Number(value)
})
let quoteTimer: ReturnType<typeof setTimeout> | undefined,
  quoteGeneration = 0
watch([mode, provider, model, budget, outputProfile], () => {
  planningConsent.value = false
  clearTimeout(quoteTimer)
  const generation = ++quoteGeneration
  quote.value = undefined
  quoteError.value = ''
  quotePending.value = mode.value === 'real'
  if (mode.value !== 'real') return
  quoteTimer = setTimeout(async () => {
    try {
      const value = await workspaceApi.budgetPreview({
        provider: provider.value,
        model: model.value,
        output_profile: outputProfile.value,
        budget_cny: budget.value,
        max_calls: 6,
      })
      if (generation === quoteGeneration) quote.value = value
    } catch (e) {
      if (generation === quoteGeneration) quoteError.value = (e as Error).message
    } finally {
      if (generation === quoteGeneration) quotePending.value = false
    }
  }, 250)
})
onUnmounted(() => {
  clearTimeout(quoteTimer)
  ++quoteGeneration
  taskEpoch++
})
watch(outputProfile, (value) => {
  if (value === 'design_document') planning.value = 'dynamic'
})
watch(scenario, (value) => {
  if (value !== 'architecture') outputProfile.value = 'concise'
})
const planningConsent = ref(false),
  executionConsent = ref(false),
  supplement = ref(''),
  busy = ref(false),
  error = ref(''),
  notice = ref('')
const terminal = new Set(['succeeded', 'failed', 'cancelled', 'interrupted'])
const labels: Record<string, string> = {
  planning: '正在规划',
  awaiting_confirmation: '等待您确认',
  executing: '正在协作',
  succeeded: '执行完成',
  failed: '任务失败',
  cancelled: '已取消',
  interrupted: '服务中断，未自动续跑',
}
const changed = computed(() => JSON.stringify(draft.value) !== JSON.stringify(task.value?.plan))
const markdown = computed(() => (task.value ? deliveryMarkdown(task.value) : ''))
let taskEpoch = 0
const pendingLoads = new Map<string, Promise<void>>()
async function perform(action: (current: () => boolean) => Promise<void>) {
  if (busy.value) return
  const epoch = taskEpoch
  const current = () => epoch === taskEpoch
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    await action(current)
  } catch (e) {
    if (current()) error.value = (e as Error).message
  } finally {
    if (current()) busy.value = false
  }
}
function accept(value: WorkspaceTask) {
  if (route.params.id && route.params.id !== value.id) return
  task.value = value
  draft.value = value.plan ? JSON.parse(JSON.stringify(value.plan)) : undefined
  executionConsent.value = false
}
function load(): Promise<void> {
  const identifier = String(route.params.id ?? ''), epoch = taskEpoch, key = `${epoch}:${identifier}`
  if (pendingLoads.has(key)) return pendingLoads.get(key)!
  const work = (async () => {
    const [value, items] = await Promise.all([identifier ? workspaceApi.task(identifier) : Promise.resolve(undefined), workspaceApi.history()])
    if (epoch !== taskEpoch || identifier !== String(route.params.id ?? '')) return
    if (value && value.id !== identifier) throw new Error('Task identity mismatch')
    if (value) { task.value = value; if (!draft.value && value.plan) draft.value = JSON.parse(JSON.stringify(value.plan)) }
    history.value = items
  })()
  pendingLoads.set(key,work); void work.then(()=>pendingLoads.delete(key),()=>pendingLoads.delete(key)); return work
}
onMounted(() =>
  perform(async () => {
    cap.value = await workspaceApi.capabilities()
    await load()
  }),
)
watch(
  () => route.params.id,
  () => {
    taskEpoch++
    busy.value = false
    error.value = ''
    task.value = undefined
    draft.value = undefined
    executionConsent.value = false
    void perform(load)
  },
)
const polling = usePolling(async () => {
  if (task.value && ['planning', 'executing'].includes(task.value.state) && !busy.value) {
    try {
      await load()
    } catch (e) {
      error.value = (e as Error).message
    }
  }
})
onMounted(polling.start)
function parseData() {
  if (scenario.value === 'document') return { text: documentText.value }
  if (scenario.value === 'schedule')
    return {
      tasks: taskIds.value
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean),
      dependencies: dependencies.value
        .split('\n')
        .filter((s) => s.trim())
        .map((s) => s.split('>').map((x) => x.trim())),
      ...(windows.value.trim()
        ? {
            windows: windows.value
              .split('\n')
              .filter(Boolean)
              .map((s) => {
                const [id, min, max] = s.split(',')
                return { task: id.trim(), min: Number(min), max: Number(max) }
              }),
          }
        : {}),
    }
  return {}
}
async function create() {
  await perform(async current => {
    const value = await workspaceApi.create({
      request: goal.value,
      scenario: scenario.value,
      data: parseData(),
      mode: mode.value,
      planning: planning.value,
      provider: provider.value,
      model: model.value,
      budget_cny: budget.value,
      max_calls: 6,
      knowledge_base_ids: knowledgeIds.value,
      ...(outputProfile.value === 'design_document' && knowledgeIds.value.length
        ? { knowledge_contract: sourceContract.value } : {}),
      ...(outputProfile.value === 'design_document' ? { output_profile: outputProfile.value } : {}),
      authorize_real: planningConsent.value,
    })
    if (!current()) return
    await router.push('/workspace/' + value.id)
    await load()
  })
}
async function refine() {
  if (!task.value) return
  await perform(async current => {
    const result = await workspaceApi.revise(task.value!.id,{revision:task.value!.revision,request:supplement.value,authorize_real:planningConsent.value})
    if (!current()) return
    accept(result); supplement.value=''; await load()
  })
}
async function edit() {
  if (!task.value || !draft.value) return
  await perform(async current => {
    const result=await workspaceApi.edit(task.value!.id,{revision:task.value!.revision,plan:draft.value!})
    if (current()) accept(result)
  })
}
async function confirm() {
  if (!task.value) return
  await perform(async current => {
    const result=await workspaceApi.confirm(task.value!.id,{revision:task.value!.revision,authorize_real:executionConsent.value})
    if (!current()) return
    accept(result); await load()
  })
}
function deps(step: TaskPlan['steps'][number], id: string, event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  step.depends_on = checked ? [...step.depends_on, id] : step.depends_on.filter((x) => x !== id)
  step.input_from = [...step.depends_on]
}
async function cancel() {
  if (!task.value) return
  await perform(async current => {
    const result=await workspaceApi.cancel(task.value!.id)
    if (current()) { accept(result); await load() }
  })
}
async function freshDelivery() {
  const identifier = String(route.params.id ?? ''), epoch = taskEpoch
  if (!identifier) throw new Error('请先打开具体任务')
  const value = await workspaceApi.task(identifier)
  if (epoch !== taskEpoch || route.params.id !== identifier || value.id !== identifier)
    throw new Error('任务已切换，请在当前任务重新导出')
  const text = deliveryMarkdown(value)
  if (value.state !== 'succeeded' || !text) throw new Error('当前任务尚无已验证的最终成果')
  accept(value)
  return { value, text }
}
async function copy() {
  await perform(async () => {
    const { text } = await freshDelivery()
    await navigator.clipboard.writeText(text)
    notice.value = 'Markdown 已复制'
  })
}
async function download(format: 'md' | 'json') {
  await perform(async () => {
    const { value, text } = await freshDelivery()
    const blob = new Blob([format === 'md' ? text : JSON.stringify(value.delivery, null, 2)], {
      type: format === 'md' ? 'text/markdown;charset=utf-8' : 'application/json',
    })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = value.id + '.' + format
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  })
}
function resubmit() {
  if (!task.value) return
  goal.value = task.value.messages.join('\n')
  scenario.value = task.value.scenario
  documentText.value = String(task.value.data.text ?? '')
  taskIds.value = Array.isArray(task.value.data.tasks) ? task.value.data.tasks.join(',') : 'A,B,C,D'
  dependencies.value = Array.isArray(task.value.data.dependencies)
    ? task.value.data.dependencies.map((p) => (Array.isArray(p) ? p.join('>') : '')).join('\n')
    : ''
  windows.value = Array.isArray(task.value.data.windows)
    ? task.value.data.windows
        .map((w) => {
          const value = w as Record<string, Json>
          return [value.task, value.min, value.max].join(',')
        })
        .join('\n')
    : ''
  mode.value = 'mock'
  planningConsent.value = false
  void router.push('/workspace')
}
</script>
<template>
  <div class="workspace-page">
    <header class="workspace-intro">
      <span class="tiny-chip">FUTURECUP · 智能协作</span>
      <h1>说出目标，让智能体一起完成。</h1>
      <p>先规划、由您确认，再协作执行。成果保留验证与技术审计入口。</p>
      <RouterLink to="/professional" class="text-link">进入专业编排工作台 →</RouterLink>
    </header>
    <div v-if="error" class="error-banner" role="alert">
      {{ error }}<span v-if="!cap"> · 当前可能是只读服务，请启动 11-A 交互服务。</span>
    </div>
    <p v-if="notice" role="status">{{ notice }}</p>
    <div class="workspace-layout">
      <main>
        <section v-if="!task" class="panel workspace-panel">
          <h2>您想完成什么？</h2>
          <div class="workspace-scenarios">
            <button
              v-for="s in [
                { id: 'architecture', name: '需求与架构方案' },
                { id: 'document', name: '文档结构化审查' },
                { id: 'schedule', name: '约束任务规划' },
              ]"
              :key="s.id"
              :class="{ active: scenario === s.id }"
              @click="scenario = s.id"
            >
              {{ s.name }}
            </button>
          </div>
          <label
            >任务目标<textarea
              v-model="goal"
              maxlength="1500"
              rows="4"
              placeholder="例如：分析需求；设计模块；并行审查约束。"
              aria-label="任务目标"
            />
          </label>
          <label v-if="scenario === 'document'"
            >粘贴文档（最多 2000 字符，不读取文件）<textarea
              v-model="documentText"
              maxlength="2000"
              rows="5"
              aria-label="待审查文档"
            />
          </label>
          <template v-if="scenario === 'schedule'"
            ><label
              >任务 ID（2～8 个大写字母，逗号分隔）<input
                v-model="taskIds"
                aria-label="任务集合" /></label
            ><label
              >前置依赖（每行 A&gt;C）<textarea
                v-model="dependencies"
                rows="3"
                aria-label="前置依赖"
              /></label
            ><label>位置窗口，可选（每行 C,2,3）<textarea v-model="windows" rows="2" /></label
            ><small>支持完整排列、依赖和位置窗口，不承诺任意约束或全局最优。</small></template
          >
          <div class="workspace-options">
            <label
              >使用知识库（可多选）<select v-model="knowledgeIds" multiple>
                <option
                  v-for="kb in knowledgeBases"
                  :disabled="kb.document_count === 0"
                  :key="kb.id"
                  :value="kb.id"
                >
                  {{ kb.name }} · {{ kb.document_count }} 文档 · v{{ kb.version }} ·
                  {{ kb.id.slice(0, 8) }}
                </option></select
              ><RouterLink to="/knowledge">管理资料与检索</RouterLink></label
            >
            <label v-if="scenario === 'architecture'"
              >成果深度<select v-model="outputProfile">
                <option value="concise">简洁结构化结果</option>
                <option value="design_document">
                  完整系统设计文档（动态规划，较高 Token 预算）
                </option>
              </select></label
            >
            <label
              >规划方式<select v-model="planning">
                <option value="dynamic">模型结构化规划（Mock 为显式模拟）</option>
                <option value="template" :disabled="outputProfile === 'design_document'">
                  场景模板（固定三步，非动态规划）
                </option>
              </select></label
            ><label
              >模型模式<select v-model="mode">
                <option value="mock">Mock 本地模拟 · 无 API 费用</option>
                <option value="real" :disabled="!cap?.real_available">
                  真实模型 · 需服务端启用与授权
                </option>
              </select></label
            >
          </div>
          <details v-if="mode === 'real'">
            <summary>本次真实规划授权与预算</summary>
            <label>服务端 Provider<input v-model="provider" /></label
            ><label>已配置模型<input v-model="model" /></label
            ><label
              >本次任务预算（人民币，默认 0.5 元）<select
                v-model="budgetChoice"
                aria-label="任务预算档位"
              >
                <option value="0.5">0.5 元</option>
                <option value="1">1 元</option>
                <option value="2">2 元（本次文档测试建议，需手动选择）</option>
                <option value="5">5 元</option>
                <option value="custom">自定义</option></select
              ><input
                v-if="budgetChoice === 'custom'"
                v-model.number="budget"
                type="number"
                min="0.01"
                max="1000"
                step="0.01"
            /></label>
            <p>用户预算：¥{{ budget }}（自定义范围大于 0、最多 1000 元）</p>
            <p v-if="quotePending">正在读取服务端配置估算，不调用模型…</p>
            <p v-else-if="quote">
              保守准入预估：{{
                quote.estimated_admission_upper_cny === null
                  ? '未知，禁止调用'
                  : '¥' + quote.estimated_admission_upper_cny.toFixed(6)
              }}
              · 服务端上限：¥{{ quote.server_budget_cny }} ·
              {{ quote.admitted ? '预算预检通过' : '未通过；检查用户预算和服务端上限' }}
            </p>
            <p v-if="quoteError" class="error-text">预算预检失败：{{ quoteError }}</p>
            <label
              ><input v-model="planningConsent" type="checkbox" />
              我确认价格与预算，授权本次规划调用；执行仍需另行确认。估算不是账单硬上限。</label
            >
          </details>
          <p class="small muted">
            最多 3 节点、总计 6
            次调用，规划/补充/执行/修复共享；规划校验和执行合计最多一次修复。无工具、联网或文件权限。
          </p>
          <label v-if="outputProfile === 'design_document' && knowledgeIds.length">
            Knowledge contract
            <select v-model="sourceContract" data-testid="source-contract">
              <option value="staged-rules-v1">v1 (compatibility)</option>
              <option value="staged-rules-v2">v2 (deterministic source binding)</option>
            </select>
          </label>
          <p v-if="outputProfile === 'design_document'" class="small muted">
            请选择模型结构化规划。规划最多 2048、前置节点各 2048、最终节点 4096 输出
            Tokens；修复仍共享六次调用和您显式选择的预算（默认
            ¥0.50）。仅验证格式、长度与章节覆盖，不证明设计正确。\n            选择知识库后采用分阶段交付：规则提取 → 设计关联 → 最终正文全局验证；来源与覆盖检查不等于设计正确性证明。
          </p>
          <button
            class="button primary"
            :disabled="
              busy ||
              !cap ||
              !goal.trim() ||
              (mode === 'real' && (!planningConsent || quotePending || !quote?.admitted))
            "
            @click="create"
          >
            {{ busy ? '正在提交…' : '生成可审查的计划' }}
          </button>
        </section>
        <template v-if="task">
          <section class="panel workspace-panel">
            <div class="section-toolbar">
              <h2>{{ labels[task.state] ?? task.state }}</h2>
              <small :data-task-id="task.id"
                >任务 {{ task.id }} · Run {{ task.run_id ?? '尚未执行' }}</small
              >
              <span class="tiny-chip">{{
                task.simulated === true
                  ? 'MOCK 模拟'
                  : task.simulated === false
                    ? '真实模型记录'
                    : '模型模式未知'
              }}</span>
            </div>
            <p>
              {{
                task.planning === 'template'
                  ? '场景模板模式'
                  : task.mode === 'real'
                    ? '真实模型动态规划；程序校验计划，确认后才执行协作任务。'
                    : 'Mock 模拟规划，仅验证接口与流程，非真实推理'
              }}
            </p>
            <h3>您的需求</h3>
            <p v-for="(text, i) in task.messages" :key="i" class="workspace-text">{{ text }}</p>
            <p v-if="task.error_code" class="error-text">实际错误：{{ task.error_code }}</p>
            <p v-if="task.knowledge_warning" class="error-text">{{ task.knowledge_warning }}</p>
            <p v-if="task.error_phase" class="error-text">
              {{
                task.error_phase === 'execution'
                  ? '协作执行阶段失败；规划已通过。'
                  : '任务规划阶段失败；尚未执行协作任务。'
              }}
            </p>
            <ul v-if="task.error_details?.length" class="error-text">
              <li v-for="(issue, i) in task.error_details" :key="i">
                {{ issue.path }}：{{ issue.message }}
              </li>
            </ul>
            <details v-if="task.planning_verifications?.length">
              <summary>查看规划校验与反馈修复审计</summary>
              <pre class="workspace-text">{{
                JSON.stringify(
                  {
                    attempts: task.planning_attempts,
                    verifications: task.planning_verifications,
                    feedback: task.planning_feedback,
                  },
                  null,
                  2,
                )
              }}</pre>
            </details>
            <button
              v-if="!terminal.has(task.state)"
              class="button secondary"
              :disabled="busy"
              @click="cancel"
            >
              取消本任务</button
            ><button v-else class="button secondary" @click="resubmit">
              带入需求重新提交（新任务，默认 Mock）
            </button>
          </section>
          <section v-if="draft" class="panel workspace-panel">
            <h2>{{ draft.name }}</h2>
            <p>{{ draft.goal }}</p>
            <p>预期交付：{{ draft.expected_delivery }}</p>
            <article v-for="step in draft.steps" :key="step.id" class="workspace-step">
              <h3>{{ step.title }}</h3>
              <template v-if="task.state === 'awaiting_confirmation'"
                ><label>子任务名称<input v-model="step.title" maxlength="300" /></label
                ><label>Agent 角色<input v-model="step.role" maxlength="300" /></label
                ><label
                  >职责<textarea v-model="step.responsibilities" maxlength="300" rows="2" /></label
                ><span>读取这些上游真实产物：</span
                ><label
                  v-for="other in draft.steps.filter((s) => s.id !== step.id)"
                  :key="other.id"
                  class="workspace-dep"
                  ><input
                    type="checkbox"
                    :checked="step.depends_on.includes(other.id)"
                    @change="deps(step, other.id, $event)"
                  />{{ other.title }}</label
                ></template
              >
              <p v-else>{{ step.role }} · {{ step.responsibilities }}</p>
              <small
                >能力：{{ step.skills.join('、') }} · 验证：{{ step.verification }} ·
                无工具权限</small
              >
              <p class="small muted">
                {{
                  step.input_from.length
                    ? step.input_from.join(' + ') + ' → ' + step.id
                    : '用户输入 → ' + step.id
                }}
              </p>
            </article>
            <template v-if="task.state === 'awaiting_confirmation'"
              ><label>补充需求<textarea v-model="supplement" maxlength="1500" rows="2" /></label
              ><label v-if="task.mode === 'real'"
                ><input v-model="planningConsent" type="checkbox" />
                授权补充后的真实规划，计入原预算</label
              >
              <div class="section-toolbar">
                <button
                  class="button secondary"
                  :disabled="
                    busy || !supplement.trim() || (task.mode === 'real' && !planningConsent)
                  "
                  @click="refine"
                >
                  补充并重新规划</button
                ><button class="button secondary" :disabled="busy || !changed" @click="edit">
                  保存计划修改并校验
                </button>
              </div>
              <label v-if="task.mode === 'real'"
                ><input v-model="executionConsent" type="checkbox" /> 我确认本版计划及本任务 ¥{{
                  task.budget_cny
                }}
                预算，授权真实执行</label
              ><button
                class="button primary"
                :disabled="busy || changed || (task.mode === 'real' && !executionConsent)"
                @click="confirm"
              >
                确认计划并开始执行</button
              ><small v-if="changed">请先保存并通过程序校验。</small></template
            >
          </section>
          <section
            v-if="task.delivery && markdown"
            class="panel workspace-panel workspace-delivery"
          >
            <span class="tiny-chip">已通过配置的独立规则</span>
            <h2>{{ task.delivery.title }}</h2>
            <details v-if="task.knowledge_sources?.length">
              <summary>本次检索来源（引用存在性不证明观点正确）</summary>
              <article v-for="s in task.knowledge_sources" :key="s.id">
                <strong>{{ s.document_name }} · {{ s.locator }} · {{ s.id }}</strong>
                <p class="workspace-text">{{ s.text }}</p>
              </article>
            </details>
            <MarkdownDocument v-if="task.delivery.markdown" :source="task.delivery.markdown" />
            <p class="workspace-text">{{ task.delivery.summary }}</p>
            <ul>
              <li v-for="(item, i) in task.delivery.items" :key="i">{{ item }}</li>
            </ul>
            <table v-if="task.delivery.order">
              <thead>
                <tr>
                  <th>顺序</th>
                  <th>任务</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(item, i) in task.delivery.order" :key="item">
                  <td>{{ i + 1 }}</td>
                  <td>{{ item }}</td>
                </tr>
              </tbody>
            </table>
            <details v-if="task.delivery_sources?.length">
              <summary>Program-restored source evidence (separate from model design)</summary>
              <blockquote v-for="s in task.delivery_sources" :key="s.source_ref">
                <p>{{ s.quote }}</p>
                <small>{{ s.document_name }} / v{{ s.document_version }} / {{ s.start }}:{{ s.end }}</small>
              </blockquote>
            </details>
            <blockquote v-for="(e, i) in task.delivery.evidence" :key="i">
              <p>{{ e.quote }}</p>
              <small>{{ e.comment }}</small>
            </blockquote>
            <p class="small muted">规则通过不等于语义普遍正确。Mock 产物不是实际推理结果。</p>
            <div class="section-toolbar">
              <button class="button" @click="copy">复制 Markdown</button
              ><button class="button secondary" :disabled="busy" @click="download('md')">
                导出 Markdown</button
              ><button class="button secondary" :disabled="busy" @click="download('json')">
                导出 JSON
              </button>
            </div>
          </section>
        </template>
      </main>
      <aside>
        <section class="panel workspace-panel">
          <h3>历史任务</h3>
          <RouterLink to="/workspace" class="text-link">＋ 新任务</RouterLink>
          <p v-if="!history.length">尚无任务记录。</p>
          <RouterLink
            v-for="item in history"
            :key="item.id"
            :to="'/workspace/' + item.id"
            class="workspace-history"
            ><strong>{{ item.name ?? item.scenario }}</strong
            ><small
              >{{ labels[item.state] ?? item.state }} ·
              {{
                item.simulated === true ? 'Mock' : item.simulated === false ? '真实记录' : '待确认'
              }}</small
            ></RouterLink
          >
        </section>
        <section v-if="task" class="panel workspace-panel">
          <h3>协作进展 · 后端真实状态</h3>
          <div v-for="node in task.report?.nodes ?? []" :key="node.id" class="workspace-node">
            <strong>{{
              draft?.steps.find((s) => s.id === node.node_id)?.title ?? node.node_id
            }}</strong><StatusBadge :state="node.state" /><small>{{
              node.resolved_model ?? node.model ?? '尚未调用'
            }}</small>
            <small
              >验证：{{ node.verified ? '通过配置规则' : '未通过或尚未验证' }} · 尝试
              {{ node.attempt_ids.length }} · 反馈 {{ node.feedback_ids.length }}</small
            >
          </div>
          <p v-if="!task.report">
            {{ task.state === 'planning' ? '正在等待规划结果；不会自动执行。' : '计划尚未执行。' }}
          </p>
          <p>
            总调用 {{ task.usage?.model_calls ?? 0 }} / {{ task.max_calls }} · 修复
            {{ task.usage?.feedback_loops ?? 0 }} / 1
          </p>
          <p>API 已报告 Tokens：{{ task.usage?.reported_tokens ?? 0 }}</p>
          <p>本任务用户预算：¥{{ task.budget_cny }}</p>
          <p v-if="task.budget_preview">
            提交时保守准入预估：¥{{
              task.budget_preview.estimated_admission_upper_cny?.toFixed(6) ?? '未知'
            }}
          </p>
          <small v-if="task.usage?.unknown_usage_calls">存在未报告用量，不能视为零消耗。</small>
          <p>
            {{
              task.mode === 'mock'
                ? 'Mock 无 API 计费'
                : '配置估算预留：¥' + task.estimated_cny_reserved.toFixed(4) + '，非实付或硬预算'
            }}
          </p>
          <ul>
            <li v-for="(e, i) in task.events" :key="i">
              {{ e.type }} · {{ new Date(e.at).toLocaleTimeString() }}
            </li>
          </ul>
          <RouterLink v-if="task.run_id" :to="'/runs/' + task.run_id" class="text-link"
            >查看节点、验证与反馈的完整技术审计 →</RouterLink
          >
        </section>
      </aside>
    </div>
  </div>
</template>
<style scoped>
.workspace-intro {
  padding: 16px 0 26px;
}
.workspace-intro h1 {
  font-size: 30px;
  color: #163e32;
  margin: 12px 0;
}
.workspace-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 22px;
}
.workspace-layout main,
.workspace-layout aside {
  min-width: 0;
}
.workspace-panel {
  padding: 24px;
  margin-bottom: 20px;
}
.workspace-scenarios {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin: 18px 0;
}
.workspace-scenarios button {
  padding: 12px;
  border: 1px solid #d3e6dc;
  border-radius: 8px;
  background: #fff;
  color: #315447;
}
.workspace-scenarios .active {
  background: #e7f5ee;
  border-color: #07866e;
}
.workspace-options {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}
.workspace-panel label {
  display: block;
  margin: 14px 0;
  font-size: 13px;
  color: #355447;
}
.workspace-panel input:not([type='checkbox']),
.workspace-panel textarea,
.workspace-panel select {
  display: block;
  width: 100%;
  padding: 12px;
  border: 1px solid #d5e1dc;
  border-radius: 8px;
  margin-top: 7px;
  background: #fff;
}
.workspace-panel input[type='checkbox'] {
  width: auto;
  margin-right: 7px;
}
.workspace-panel textarea {
  resize: vertical;
  min-height: 60px;
}
.workspace-text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.8;
}
.workspace-step {
  padding: 16px;
  background: #f5faf7;
  border: 1px solid #dfece4;
  border-radius: 10px;
  margin: 14px 0;
}
.workspace-dep {
  display: inline-block !important;
  margin-right: 14px !important;
}
.workspace-history {
  display: grid;
  gap: 6px;
  border-bottom: 1px solid #e4ece6;
  padding: 14px 0;
}
.workspace-history small,
.workspace-node small {
  color: #60766a;
}
.workspace-node {
  display: grid;
  gap: 7px;
  padding: 12px 0;
  border-bottom: 1px solid #e3eae6;
}
.workspace-delivery {
  border-top: 4px solid #07866e;
}
.workspace-delivery li {
  margin: 10px 0;
  line-height: 1.7;
}
.workspace-delivery blockquote {
  border-left: 3px solid #07866e;
  padding: 10px 16px;
  background: #f2f8f4;
}
.workspace-panel small {
  line-height: 1.7;
}
.workspace-panel .button {
  margin: 6px 0;
}
@media (max-width: 1050px) {
  .workspace-layout {
    grid-template-columns: 1fr;
  }
  .workspace-options {
    grid-template-columns: 1fr;
  }
}
</style>
