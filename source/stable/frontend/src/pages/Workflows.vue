<script setup lang="ts">
import {
  ref,
  reactive,
  shallowReactive,
  shallowRef,
  computed,
  onMounted,
  watch,
  nextTick,
} from 'vue'
import { useRoute, useRouter, onBeforeRouteLeave } from 'vue-router'
import { VueFlow, useVueFlow, type Node, type Edge, type Connection } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import {
  Bot,
  Plus,
  GitBranch,
  Repeat2,
  Layers,
  Merge,
  Save,
  Play,
  CheckCheck,
  Code2,
  X,
  Trash2,
  FileInput,
  ChevronDown,
  ShieldCheck,
  ArrowUpRight,
} from '@lucide/vue'
import { NModal, NSpin, useMessage, useDialog } from 'naive-ui'
import { api, ApiError } from '../api'
import type { Agent, Workflow, WorkflowNode, NodeKind, Json, Policy } from '../types'
import {
  blankWorkflow,
  demos,
  demoWorkflow,
  initializeDemos,
  canonical,
  type DemoKey,
} from '../demos'
import { kindLabels, pretty, refreshVersion } from '../composables'
import { scenarios, scenarioWorkflow, scenarioInput, type ScenarioName } from '../scenarios'
import HarnessNode from '../components/HarnessNode.vue'
import EmptyState from '../components/EmptyState.vue'
const route = useRoute(),
  router = useRouter(),
  message = useMessage(),
  dialog = useDialog()
const flow = shallowRef<Workflow>(shallowReactive(blankWorkflow())),
  definitions = shallowRef<Workflow[]>([]),
  agents = ref<Agent[]>([]),
  policies = shallowRef<Policy[]>([])
type EditorNode = Node<{ spec: WorkflowNode }> & { data: { spec: WorkflowNode } }
const nodes = shallowRef<EditorNode[]>([]),
  edges = shallowRef<Edge[]>([]),
  selected = ref(''),
  selectedEdge = ref(''),
  busy = ref(false),
  error = ref(''),
  validated = ref(''),
  saved = ref(''),
  tab = ref('canvas'),
  raw = ref(''),
  showRun = ref(false),
  runInput = ref('{"topic":"多智能体协作平台"}'),
  showRules = ref(false),
  ruleText = ref(''),
  ruleError = ref(''),
  ruleSaving = ref(false)
const formBaseline = ref('')
const form = reactive({
  agent: '',
  mode: 'text',
  input: '',
  config: '{}',
  verification: '',
  action: 'abort',
  maxAttempts: 1,
  maxLoops: 0,
  maxReplans: 0,
  child: '',
  iterations: 3,
})
const vf = useVueFlow('workflow-editor')
const selectedSpec = computed(() => nodes.value.find((n) => n.id === selected.value)?.data.spec)
const currentSignature = computed(() => canonical(serialized()))
const dirty = computed(
  () =>
    saved.value !== currentSignature.value ||
    (!!selected.value && canonical(form) !== formBaseline.value) ||
    (tab.value === 'json' && raw.value !== pretty(serialized())),
)
const palette = [
  { kind: 'task' as NodeKind, icon: Bot },
  { kind: 'condition' as NodeKind, icon: GitBranch },
  { kind: 'parallel' as NodeKind, icon: Merge },
  { kind: 'loop' as NodeKind, icon: Repeat2 },
  { kind: 'subworkflow' as NodeKind, icon: Layers },
]
function serialized(): Workflow {
  return {
    ...flow.value,
    nodes: nodes.value.map((n) => n.data.spec),
    edges: edges.value.map((e) => ({
      source: e.source,
      target: e.target,
      branch: (e.data?.branch as string) || null,
    })),
  }
}
function loadDefinition(value: Workflow) {
  if (!Array.isArray(value.nodes) || !Array.isArray(value.edges))
    throw new Error('工作流必须包含 nodes 和 edges 数组')
  if (value.nodes.length > 64) throw new Error('单个工作流最多 64 个节点')
  for (const node of value.nodes) {
    if (!node || typeof node.id !== 'string' || !palette.some((item) => item.kind === node.kind))
      throw new Error(
        '存在不支持的节点类型或无效节点 ID；仅支持 Agent、条件、汇合、受限循环和子工作流',
      )
    if (!node.config || typeof node.config !== 'object' || Array.isArray(node.config))
      throw new Error('节点 config 必须为 JSON 对象')
  }
  if (new Set(value.nodes.map((node) => node.id)).size !== value.nodes.length)
    throw new Error('节点 ID 不能重复')
  if (
    value.edges.some(
      (edge) => !edge || typeof edge.source !== 'string' || typeof edge.target !== 'string',
    )
  )
    throw new Error('依赖必须包含 source 与 target 节点 ID')
  flow.value = shallowReactive(JSON.parse(JSON.stringify(value)) as Workflow)
  selected.value = ''
  selectedEdge.value = ''
  error.value = ''
  validated.value = ''
  let positions: Record<string, { x: number; y: number }> = {}
  try {
    positions = JSON.parse(
      localStorage.getItem('fc-layout:' + value.id + '@' + value.version) || '{}',
    )
  } catch {
    positions = {}
  }
  nodes.value = value.nodes.map((spec, i) => ({
    id: spec.id,
    type: 'harness',
    position: positions[spec.id] ?? { x: 80 + (i % 3) * 300, y: 90 + Math.floor(i / 3) * 180 },
    data: { spec: JSON.parse(JSON.stringify(spec)) },
  }))
  edges.value = value.edges.map((e, i) => ({
    id: 'edge-' + i,
    source: e.source,
    target: e.target,
    label: e.branch ?? '',
    type: 'smoothstep',
    data: { branch: e.branch ?? null },
  }))
  saved.value = canonical(serialized())
  raw.value = pretty(serialized())
  nextTick(() => vf.fitView({ padding: 0.25, duration: 200 }))
}
async function load() {
  error.value = ''
  try {
    const result = await Promise.all([api.workflows(), api.agents(), api.policies()])
    ;[definitions.value, agents.value, policies.value] = result
  } catch (e) {
    error.value = (e as Error).message
  }
}
function discard(action: () => void) {
  if (dirty.value && nodes.value.length)
    dialog.warning({
      title: '切换工作流',
      content: '当前画布有未保存修改，切换后将丢弃这些修改。',
      positiveText: '丢弃并切换',
      negativeText: '继续编辑',
      onPositiveClick: action,
    })
  else action()
}
function choose(value: Workflow) {
  discard(() => loadDefinition(value))
}
function add(kind: NodeKind) {
  const id = kind + '_' + (nodes.value.length + 1) + '_' + Date.now().toString().slice(-4)
  const config: WorkflowNode['config'] =
    kind === 'task'
      ? { agent_id: agents.value[0]?.id ?? '', output_mode: 'text' }
      : kind === 'condition'
        ? { predicate: { left: { $ref: 'input.approved' }, op: 'eq', right: true } }
        : kind === 'parallel'
          ? { join: 'all_selected' }
          : kind === 'loop'
            ? {
                workflow: { id: '', version: '1' },
                max_iterations: 3,
                until: { left: { $ref: 'result.done' }, op: 'eq', right: true },
              }
            : { workflow: { id: '', version: '1' } }
  nodes.value = [
    ...nodes.value,
    {
      id,
      type: 'harness',
      position: {
        x: 80 + (nodes.value.length % 3) * 290,
        y: 100 + Math.floor(nodes.value.length / 3) * 180,
      },
      data: { spec: { id, kind, config } },
    },
  ]
  select(id)
  validated.value = ''
}
function select(id: string) {
  if (selected.value && selected.value !== id && applyNode() === false) return
  selected.value = id
  selectedEdge.value = ''
  const spec = nodes.value.find((n) => n.id === id)!.data.spec,
    c = spec.config
  form.agent = String(c.agent_id ?? '')
  form.mode = String(c.output_mode ?? 'text')
  form.input = pretty(c.input ?? { $ref: 'input' })
  form.config = pretty(c)
  form.verification = c.verification ? canonical(c.verification) : ''
  const recovery = c.recovery as Record<string, Json> | undefined
  form.action = String(recovery?.on_failure ?? 'abort')
  form.maxAttempts = Number(recovery?.max_attempts ?? 1)
  form.maxLoops = Number(recovery?.max_feedback_loops ?? 0)
  form.maxReplans = Number(recovery?.max_replans ?? 0)
  form.child = c.workflow ? canonical(c.workflow) : ''
  form.iterations = Number(c.max_iterations ?? 3)
  formBaseline.value = canonical(form)
}
function parseObject(text: string): Record<string, Json> {
  const value = JSON.parse(text)
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new Error('配置必须为 JSON 对象')
  return value
}
function applyNode() {
  if (!selectedSpec.value) return
  try {
    const spec = selectedSpec.value,
      c = parseObject(form.config)
    c.input = JSON.parse(form.input)
    if (spec.kind === 'task') {
      if (form.agent) {
        c.agent_id = form.agent
        delete c.candidate_agents
      } else {
        delete c.agent_id
        delete c.agent_version
        if (!c.candidate_agents && !c.required_skills && !c.task_type)
          c.candidate_agents = agents.value.map((a) => a.id)
      }
      c.output_mode = form.mode
      if (form.verification) {
        c.verification = JSON.parse(form.verification)
        c.recovery = {
          ...((c.recovery as Record<string, Json>) ?? {}),
          on_failure: form.action,
          max_attempts: form.maxAttempts,
          max_feedback_loops: form.maxLoops,
          max_replans: form.maxReplans,
        }
      } else {
        delete c.verification
        delete c.recovery
      }
    }
    if (spec.kind === 'subworkflow' || spec.kind === 'loop') {
      if (form.child) c.workflow = JSON.parse(form.child)
      if (spec.kind === 'loop') c.max_iterations = form.iterations
    }
    spec.config = c
    nodes.value = nodes.value.map((n) =>
      n.id === spec.id ? { ...n, data: { spec: { ...spec, config: c } } } : n,
    )
    form.config = pretty(c)
    formBaseline.value = canonical(form)
    validated.value = ''
    return true
  } catch (e) {
    error.value = '节点配置无效：' + (e as Error).message
    return false
  }
}
function connect(connection: Connection) {
  if (!connection.source || !connection.target) return
  const source = nodes.value.find((n) => n.id === connection.source)
  const branch = source?.data.spec.kind === 'condition' ? 'true' : null
  edges.value = [
    ...edges.value,
    {
      id: 'edge-' + crypto.randomUUID(),
      source: connection.source,
      target: connection.target,
      type: 'smoothstep',
      label: branch ?? '',
      data: { branch },
    },
  ]
  validated.value = ''
}
function updateBranch(value: string) {
  const edge = edges.value.find((e) => e.id === selectedEdge.value)
  if (edge) {
    edges.value = edges.value.map((e) =>
      e.id === edge.id ? { ...e, data: { branch: value || null }, label: value } : e,
    )
    validated.value = ''
  }
}
function clearSelection() {
  if (applyNode() === false) return
  selected.value = ''
  selectedEdge.value = ''
}
function selectEdge(id: string) {
  if (applyNode() === false) return
  selectedEdge.value = id
  selected.value = ''
}
function removeSelected() {
  if (selectedEdge.value) {
    edges.value = edges.value.filter((e) => e.id !== selectedEdge.value)
    selectedEdge.value = ''
  } else if (selected.value) {
    edges.value = edges.value.filter(
      (e) => e.source !== selected.value && e.target !== selected.value,
    )
    nodes.value = nodes.value.filter((n) => n.id !== selected.value)
    selected.value = ''
  }
  validated.value = ''
}
function getValue() {
  if (tab.value === 'json') {
    const value = JSON.parse(raw.value) as Workflow
    if (!Array.isArray(value.nodes) || !Array.isArray(value.edges))
      throw new Error('工作流必须包含 nodes 与 edges 数组')
    const previousSaved = saved.value
    loadDefinition({ ...blankWorkflow(), ...value })
    saved.value = previousSaved
    tab.value = 'canvas'
  } else if (applyNode() === false) throw new Error('请修正节点配置')
  return serialized()
}
async function validate() {
  busy.value = true
  error.value = ''
  try {
    const value = JSON.parse(JSON.stringify(getValue())) as Workflow
    await api.validateWorkflow(value)
    validated.value = canonical(value)
    message.success('后端工作流预检通过（尚未执行）')
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}
async function persist() {
  const value = JSON.parse(JSON.stringify(getValue())) as Workflow
  await api.validateWorkflow(value)
  let existing: Workflow | undefined
  try {
    existing = await api.workflow(value.id, value.version)
  } catch (e) {
    if (!(e instanceof ApiError && e.status === 404)) throw e
  }
  if (existing) {
    if (canonical(existing) !== canonical(value))
      throw new Error('此工作流版本已存在且不可覆盖。请修改版本号后保存。')
  } else await api.saveWorkflow(value)
  saved.value = canonical(value)
  validated.value = canonical(value)
  try {
    localStorage.setItem(
      'fc-layout:' + value.id + '@' + value.version,
      JSON.stringify(Object.fromEntries(nodes.value.map((n) => [n.id, n.position]))),
    )
  } catch {
    message.warning('工作流已保存，但浏览器无法保存画布位置。')
  }
  await load()
  return value
}
async function save() {
  busy.value = true
  error.value = ''
  try {
    await persist()
    message.success('工作流版本已保存')
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}
async function execute() {
  busy.value = true
  error.value = ''
  try {
    const input = JSON.parse(runInput.value),
      value = await persist()
    const run = await api.execute(value.id, value.version, input)
    showRun.value = false
    saved.value = canonical(serialized())
    await router.push('/runs/' + run.run.id)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}
async function init() {
  busy.value = true
  error.value = ''
  try {
    await initializeDemos()
    await load()
    message.success('示例 Agent 和验证规则已就绪；没有自动执行模型。')
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}
function loadDemo(key: DemoKey) {
  discard(() => loadDefinition(demoWorkflow(key)))
}
function switchTab(value: string) {
  if (value === 'json') {
    if (applyNode() === false) return
    raw.value = pretty(serialized())
  } else if (tab.value === 'json') {
    try {
      getValue()
    } catch (e) {
      error.value = (e as Error).message
      return
    }
  }
  tab.value = value
}
async function createRule() {
  if (ruleSaving.value) return
  ruleSaving.value = true
  ruleError.value = ''
  try {
    await api.createPolicy(JSON.parse(ruleText.value))
    policies.value = await api.policies()
    showRules.value = false
    message.success('不可变规则版本已创建')
  } catch (e) {
    ruleError.value = (e as Error).message
  } finally {
    ruleSaving.value = false
  }
}
function openRules() {
  ruleText.value = pretty({
    id: 'policy-' + Date.now(),
    version: '1',
    name: '结构验证规则',
    checks: [{ kind: 'json_schema', config: { schema: { type: 'object', required: ['answer'] } } }],
  })
  showRules.value = true
}
onMounted(async () => {
  await load()
  const key = route.query.demo as DemoKey
  if (demos.some((d) => d.key === key)) loadDefinition(demoWorkflow(key))
  const scenario = route.query.scenario as ScenarioName
  if (scenario && scenario in scenarios) {
    loadDefinition(scenarioWorkflow(scenario))
    runInput.value = pretty(scenarioInput(scenario))
  }
})
watch(refreshVersion, load)
onBeforeRouteLeave(() => {
  if (dirty.value && nodes.value.length) return window.confirm('工作流有未保存修改，确认离开？')
})
</script>
<template>
  <div class="workflow-page">
    <div v-if="error" class="error-banner" role="alert">
      {{ error }}<button @click="error = ''" aria-label="关闭错误"><X :size="15" /></button>
    </div>
    <div class="demo-strip">
      <div><span class="tiny-chip">MOCK 示例</span><span>可重复初始化，接入真实执行引擎</span></div>
      <div>
        <button
          v-for="demo in demos"
          :key="demo.key"
          :title="demo.description"
          @click="loadDemo(demo.key)"
        >
          {{ demo.name }}<ArrowUpRight :size="12" /></button
        ><button class="button secondary small-button" :disabled="busy" @click="init">
          初始化示例依赖
        </button>
      </div>
    </div>
    <div class="workflow-toolbar panel">
      <div class="workflow-identity">
        <GitBranch :size="20" /><input
          v-model="flow.name"
          aria-label="工作流名称"
          placeholder="工作流名称"
        /><span class="version">{{ dirty ? '未保存修改' : '草稿 / 已加载' }}</span>
      </div>
      <div class="toolbar-right">
        <button class="button secondary" :disabled="busy" @click="validate">
          <CheckCheck :size="15" />合法性检查</button
        ><button class="button secondary" :disabled="busy" @click="save">
          <Save :size="15" />保存版本</button
        ><button class="button primary" :disabled="busy || !nodes.length" @click="showRun = true">
          <Play :size="15" />执行工作流
        </button>
      </div>
    </div>
    <div class="editor-shell">
      <aside class="editor-library">
        <div class="library-heading"><strong>节点组件</strong><span>点击添加</span></div>
        <button
          v-for="item in palette"
          :key="item.kind"
          class="palette-item"
          :draggable="true"
          @dragstart="(e) => e.dataTransfer?.setData('application/futurecup-node', item.kind)"
          @click="add(item.kind)"
        >
          <component :is="item.icon" :size="17" /><span>{{ kindLabels[item.kind] }}</span
          ><Plus :size="14" />
        </button>
        <p class="helper">
          并行：无依赖节点自动并发；汇合节点控制等待语义。验证配置在 Agent
          节点中，不创建虚假验证节点。
        </p>
        <div class="library-heading">
          <strong>已保存工作流</strong
          ><button
            class="icon-button"
            title="新建空工作流"
            @click="discard(() => loadDefinition(blankWorkflow()))"
          >
            <Plus :size="16" />
          </button>
        </div>
        <div class="saved-workflows">
          <button
            v-for="item in definitions"
            :key="item.id + '@' + item.version"
            :class="{ active: item.id === flow.id && item.version === flow.version }"
            @click="choose(item)"
          >
            <GitBranch :size="14" /><span
              >{{ item.name }}<small>{{ item.id }} @ {{ item.version }}</small></span
            >
          </button>
          <p v-if="!definitions.length" class="helper">尚无已保存工作流</p>
        </div>
        <button class="text-link library-rule" @click="openRules">
          <ShieldCheck :size="15" />创建验证规则
        </button>
      </aside>
      <section class="canvas-pane">
        <div class="canvas-tabs">
          <div>
            <button :class="{ active: tab === 'canvas' }" @click="switchTab('canvas')">
              <GitBranch :size="14" />流程画布</button
            ><button :class="{ active: tab === 'json' }" @click="switchTab('json')">
              <Code2 :size="14" />JSON 定义
            </button>
          </div>
          <span
            :class="['small', validated && validated === currentSignature ? 'positive' : 'muted']"
            >{{
              validated && validated === currentSignature
                ? '后端预检通过'
                : '保存和执行前将由后端校验'
            }}</span
          >
        </div>
        <div
          v-if="tab === 'canvas'"
          class="flow-canvas"
          @dragover.prevent
          @drop.prevent="
            (e) => {
              const kind = e.dataTransfer?.getData('application/futurecup-node')
              if (palette.some((p) => p.kind === kind)) add(kind as NodeKind)
            }
          "
        >
          <VueFlow
            id="workflow-editor"
            v-model:nodes="nodes"
            v-model:edges="edges"
            :min-zoom="0.2"
            :max-zoom="2"
            :delete-key-code="null"
            @connect="connect"
            @node-click="(e) => select(e.node.id)"
            @edge-click="(e) => selectEdge(e.edge.id)"
            @pane-click="clearSelection"
            ><template #node-harness="props"
              ><HarnessNode :data="props.data" :selected="props.selected" /></template
            ><Background :gap="22" pattern-color="#c9d4d1" /><Controls /><MiniMap
              :pannable="true"
              :zoomable="true"
          /></VueFlow>
          <div v-if="!nodes.length" class="canvas-empty">
            <GitBranch :size="32" /><strong>把协作构想，连接起来</strong
            ><span>从左侧添加节点，或加载一个 Mock 示例</span>
          </div>
          <div class="canvas-footnote">
            <span>{{ nodes.length }} 个节点 · {{ edges.length }} 条依赖</span
            ><span>拖拽移动 · 滚轮缩放 · 端口连线</span>
          </div>
        </div>
        <div v-else class="json-editor-wrap">
          <p class="helper">
            修改完整 WorkflowDefinition。点击“应用 JSON”导入画布，保存时仍由后端验证。
          </p>
          <textarea
            v-model="raw"
            class="code-editor"
            aria-label="工作流 JSON 定义"
            spellcheck="false"
          /><button
            class="button primary"
            @click="
              () => {
                try {
                  getValue()
                  message.success('JSON 已应用')
                } catch (e) {
                  error = (e as Error).message
                }
              }
            "
          >
            应用 JSON
          </button>
        </div>
      </section>
      <aside class="inspector">
        <div class="library-heading">
          <strong>{{
            selectedSpec ? '节点配置' : selectedEdge ? '依赖配置' : '工作流设置'
          }}</strong><button
            v-if="selectedSpec || selectedEdge"
            class="icon-button danger-text"
            aria-label="删除选中元素"
            @click="removeSelected"
          >
            <Trash2 :size="16" />
          </button>
        </div>
        <form v-if="selectedSpec" class="inspector-form" @submit.prevent="applyNode">
          <div class="inspector-node-title">
            <span class="tiny-chip">{{ kindLabels[selectedSpec.kind] }}</span><strong
              class="mono"
              >{{ selectedSpec.id }}</strong
            >
          </div>
          <template v-if="selectedSpec.kind === 'task'"
            ><label
              >执行 Agent<select v-model="form.agent">
                <option value="">动态调度（高级配置候选集合）</option>
                <option v-for="agent in agents" :key="agent.id" :value="agent.id">
                  {{ agent.name }} · {{ agent.id }}
                </option>
              </select></label
            ><label
              >输出解析<select v-model="form.mode">
                <option value="text">文本</option>
                <option value="json">JSON</option>
                <option value="tool_result">最后一次成功工具结果</option>
              </select></label
            ><label
              >独立验证<select v-model="form.verification">
                <option value="">不启用（保持未验证）</option>
                <option
                  v-for="policy in policies"
                  :key="policy.ref.id + '@' + policy.ref.version"
                  :value="canonical(policy.ref)"
                >
                  {{ policy.name }} @ {{ policy.ref.version }}
                </option>
              </select></label
            ><template v-if="form.verification"
              ><label
                >失败恢复策略<select v-model="form.action">
                  <option value="abort">停止</option>
                  <option value="prompt_retry">原 Agent 反馈修复</option>
                  <option value="reassign">重新选择 Agent</option>
                  <option value="tool_switch">更换授权工具子集</option>
                  <option value="fallback">明确备用路径</option>
                  <option value="human">等待人工授权</option>
                </select></label
              >
              <div class="form-grid">
                <label
                  >最大尝试<input
                    v-model.number="form.maxAttempts"
                    type="number"
                    min="1"
                    max="16"
                    required /></label
                ><label
                  >反馈循环<input
                    v-model.number="form.maxLoops"
                    type="number"
                    min="0"
                    max="15"
                    required /></label
                ><label
                  >重规划次数<input
                    v-model.number="form.maxReplans"
                    type="number"
                    min="0"
                    max="15"
                    required
                /></label></div></template></template
          ><template v-if="selectedSpec.kind === 'loop' || selectedSpec.kind === 'subworkflow'"
            ><label
              >引用子工作流<select v-model="form.child">
                <option value="">请在高级配置填写引用</option>
                <option
                  v-for="item in definitions"
                  :key="item.id + '@' + item.version"
                  :value="canonical({ id: item.id, version: item.version })"
                >
                  {{ item.name }} @ {{ item.version }}
                </option>
              </select></label
            ><label v-if="selectedSpec.kind === 'loop'"
              >最大迭代次数<input
                v-model.number="form.iterations"
                type="number"
                min="1"
                max="100"
                required
            /></label>
            <p class="helper">
              循环退出条件 until 在高级配置填写，引用 result / iteration；普通 DAG 不能连成环。
            </p></template
          >
          <p v-if="selectedSpec.kind === 'condition'" class="info-note">
            在高级配置编辑 predicate；分别连出 true / false 两条依赖，并在依赖配置选择标签。
          </p>
          <p v-if="selectedSpec.kind === 'parallel'" class="info-note">
            join=all 等待所有上游成功；all_selected 允许未选分支跳过。
          </p>
          <label
            >输入映射（JSON）<textarea
              v-model="form.input"
              rows="4"
              class="mono"
              spellcheck="false"
            /></label
          ><label
            >完整节点配置（JSON）<textarea
              v-model="form.config"
              rows="11"
              class="mono"
              spellcheck="false"
            />
          </label>
          <p class="helper">
            候选 Agent、技能、工具、predicate、until、limits
            和备用节点均映射到后端原生配置。上方表单字段应用时优先。
          </p>
          <button class="button primary" type="submit">应用节点配置</button>
        </form>
        <div v-else-if="selectedEdge" class="inspector-form">
          <label
            >分支标签<select
              :value="edges.find((e) => e.id === selectedEdge)?.data?.branch ?? ''"
              @change="updateBranch(($event.target as HTMLSelectElement).value)"
            >
              <option value="">普通依赖</option>
              <option value="true">条件 true</option>
              <option value="false">条件 false</option>
              <option value="fallback">显式失败备用路径</option>
            </select></label
          >
          <p class="helper">
            只有条件节点使用 true/false。fallback 须与源节点 recovery.fallback_node
            一致；后端会拒绝非法标签。
          </p>
        </div>
        <div v-else class="inspector-form">
          <label>工作流 ID<input v-model="flow.id" maxlength="64" /></label
          ><label>版本<input v-model="flow.version" maxlength="64" /></label
          ><label
            >调度策略<select v-model="flow.scheduling_strategy">
              <option value="caas">CAAS 自适应</option>
              <option value="fixed">Fixed 固定</option>
              <option value="round_robin">Round Robin 轮询</option>
            </select></label
          ><label
            >最大并发<input
              v-model.number="flow.max_concurrency"
              type="number"
              min="1"
              max="16" /></label
          ><label
            >整体超时（秒）<input
              v-model.number="flow.timeout_seconds"
              type="number"
              min="1"
              max="300" /></label
          ><label
            >错误策略<select v-model="flow.error_policy">
              <option value="continue">continue · 保留独立分支执行</option>
              <option value="fail_fast">fail_fast · 首次失败终止</option>
            </select></label
          >
          <p class="info-note">
            配置版本不可覆盖。修改已有定义后请使用新版本；JSON 视图可配置输入/输出
            Schema、资源预算及全部高级字段。
          </p>
          <div class="inspector-hint">
            <GitBranch :size="27" />
            <p>点击画布节点或连线<br />编辑具体执行语义</p>
          </div>
        </div>
      </aside>
    </div>
    <NModal v-model:show="showRun" :mask-closable="false"
      ><section class="form-modal">
        <header>
          <h2>执行工作流</h2>
          <button class="icon-button" aria-label="关闭执行对话框" @click="showRun = false">
            <X :size="20" />
          </button>
        </header>
        <div class="modal-content">
          <p class="info-note">
            将预检并保存当前版本，然后交给后端调度执行。模拟 Provider
            会在执行报告中标记；不会自动启用真实模型。
          </p>
          <label
            >任务输入（JSON）<textarea
              v-model="runInput"
              rows="8"
              class="code-editor"
              aria-label="任务输入 JSON"
            />
          </label>
          <p v-if="error" class="error-text" role="alert">{{ error }}</p>
        </div>
        <footer>
          <button class="button secondary" @click="showRun = false">取消</button
          ><button class="button primary" :disabled="busy" @click="execute">
            <Play :size="15" />{{ busy ? '正在提交…' : '确认执行' }}
          </button>
        </footer>
      </section></NModal
    >
    <NModal v-model:show="showRules"
      ><section class="form-modal">
        <header>
          <h2>创建不可变验证规则</h2>
          <button class="icon-button" aria-label="关闭规则对话框" @click="showRules = false">
            <X :size="20" />
          </button>
        </header>
        <div class="modal-content">
          <p class="info-note">
            支持 json_schema / assertion / numeric /
            external_tests。规则版本不可覆盖；外部测试必须服务端预注册，不支持任意 Shell。
          </p>
          <textarea v-model="ruleText" rows="17" class="code-editor" aria-label="验证规则 JSON" />
          <p v-if="ruleError" class="error-text">{{ ruleError }}</p>
        </div>
        <footer>
          <button class="button primary" :disabled="ruleSaving" @click="createRule">
            创建规则版本
          </button>
        </footer>
      </section></NModal
    >
  </div>
</template>
