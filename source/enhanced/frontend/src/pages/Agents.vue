<script setup lang="ts">
import { ref, reactive, computed, onMounted, watch } from 'vue'
import {
  Bot,
  Plus,
  Search,
  Pencil,
  Fingerprint,
  Cpu,
  ShieldCheck,
  ArrowUpRight,
  X,
} from '@lucide/vue'
import { NModal, NSpin, NSwitch, useMessage, useDialog } from 'naive-ui'
import { api } from '../api'
import type { Agent, Profile, ModelCatalog } from '../types'
import { refreshVersion } from '../composables'
import StatusBadge from '../components/StatusBadge.vue'
import EmptyState from '../components/EmptyState.vue'
import JsonPanel from '../components/JsonPanel.vue'
const agents = ref<Agent[]>([]),
  profile = ref<Profile>(),
  profileError = ref(''),
  catalog = ref<ModelCatalog>(),
  busy = ref(false),
  saving = ref(false),
  error = ref(''),
  formError = ref(''),
  query = ref(''),
  filter = ref('all'),
  editing = ref<Agent>(),
  show = ref(false),
  showProfile = ref(false)
const message = useMessage(),
  dialog = useDialog()
const form = reactive({
  id: '',
  name: '',
  version: '1',
  role: '',
  responsibilities: '',
  system_prompt: '',
  skills: '',
  knowledge_base_ids: '',
  provider: 'mock' as Agent['provider'],
  model: 'mock-echo',
  response_format: 'text',
  tools: false,
  task_types: '*',
  max_concurrency: 1,
  enabled: true,
  available: true,
  estimated_cost: '',
  estimated_latency_ms: '',
})
const visible = computed(() =>
  agents.value.filter(
    (a) =>
      (filter.value === 'all' || (filter.value === 'enabled' ? a.enabled : !a.enabled)) &&
      (a.id + ' ' + a.name + ' ' + a.role).toLowerCase().includes(query.value.toLowerCase()),
  ),
)
const modelProviders = computed(() => catalog.value?.providers ?? [])
const selectedProvider = computed(() => modelProviders.value.find((p) => p.id === form.provider))
const selectedModel = computed(() =>
  selectedProvider.value?.models.find((m) => m.id === form.model),
)
function providerChanged() {
  form.model = selectedProvider.value?.models[0]?.id ?? ''
  form.response_format = 'text'
}
async function load() {
  busy.value = true
  error.value = ''
  try {
    agents.value = await api.agents()
    const value = await api.modelCatalog()
    catalog.value = value?.providers ? value : undefined
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}
function open(agent?: Agent) {
  editing.value = agent
  formError.value = ''
  Object.assign(
    form,
    agent
      ? {
          ...agent,
          version: 'v' + Date.now(),
          system_prompt: '',
          skills: agent.skills.join(', '),
          knowledge_base_ids: (agent.knowledge_base_ids ?? []).join(', '),
          tools: agent.tools.some((t) => t.id === 'calculator'),
          task_types: agent.task_types.join(', '),
          estimated_cost: agent.estimated_cost?.toString() ?? '',
          estimated_latency_ms: agent.estimated_latency_ms?.toString() ?? '',
          response_format: agent.response_format ?? 'text',
        }
      : {
          id: '',
          name: '',
          version: '1',
          role: '',
          responsibilities: '',
          system_prompt: '',
          skills: '',
          knowledge_base_ids: '',
          provider: 'mock',
          model: 'mock-echo',
          response_format: 'text',
          tools: false,
          task_types: '*',
          max_concurrency: 1,
          enabled: true,
          available: true,
          estimated_cost: '',
          estimated_latency_ms: '',
        },
  )
  show.value = true
}
const tags = (text: string) =>
  text
    .split(/[,，]/)
    .map((s) => s.trim())
    .filter(Boolean)
async function save() {
  saving.value = true
  formError.value = ''
  try {
    const payload = {
      name: form.name,
      version: form.version,
      role: form.role,
      responsibilities: form.responsibilities,
      system_prompt: form.system_prompt || undefined,
      skills: tags(form.skills),
      knowledge_base_ids: tags(form.knowledge_base_ids),
      provider: form.provider,
      model: form.model,
      response_format: form.response_format,
      tools: form.tools ? [{ id: 'calculator', version: '1' }] : [],
      task_types: tags(form.task_types),
      max_concurrency: form.max_concurrency,
      enabled: form.enabled,
      available: form.available,
      estimated_cost: form.estimated_cost === '' ? null : Number(form.estimated_cost),
      estimated_latency_ms:
        form.estimated_latency_ms === '' ? null : Number(form.estimated_latency_ms),
    }
    if (editing.value)
      await api.editAgent(form.id, { ...payload, expected_version: editing.value.version })
    else await api.createAgent({ ...payload, id: form.id })
    show.value = false
    form.system_prompt = ''
    message.success('Agent 配置已保存')
    await load()
  } catch (e) {
    formError.value = (e as Error).message
  } finally {
    saving.value = false
  }
}
async function toggle(agent: Agent) {
  dialog.warning({
    title: agent.enabled ? '禁用 Agent' : '启用 Agent',
    content: `${agent.name}：${agent.enabled ? '停止接收新的执行任务，已有记录保留。' : '创建新配置版本并恢复接收任务。'}`,
    positiveText: '确认',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        if (agent.enabled) await api.disableAgent(agent.id)
        else {
          const { id, ...payload } = agent
          await api.editAgent(id, {
            ...payload,
            enabled: true,
            version: 'v' + Date.now(),
            expected_version: agent.version,
          })
        }
        await load()
      } catch (e) {
        message.error((e as Error).message)
      }
    },
  })
}
async function viewProfile(agent: Agent) {
  showProfile.value = true
  profile.value = undefined
  profileError.value = ''
  try {
    profile.value = await api.profile(agent.id)
  } catch (e) {
    profileError.value = (e as Error).message
    message.error(profileError.value)
  }
}
onMounted(load)
watch(refreshVersion, load)
</script>
<template>
  <div>
    <div v-if="error" class="error-banner" role="alert">
      {{ error }}<button @click="load">重试</button>
    </div>
    <div class="section-toolbar">
      <div class="segmented">
        <button :class="{ selected: filter === 'all' }" @click="filter = 'all'">
          全部 Agent <span>{{ agents.length }}</span></button
        ><button :class="{ selected: filter === 'enabled' }" @click="filter = 'enabled'">
          已启用</button
        ><button :class="{ selected: filter === 'disabled' }" @click="filter = 'disabled'">
          已禁用
        </button>
      </div>
      <div class="toolbar-right">
        <div class="search-input">
          <Search :size="16" /><input
            v-model="query"
            aria-label="搜索 Agent"
            placeholder="搜索名称、ID 或角色"
          />
        </div>
        <button class="button primary" @click="open()"><Plus :size="16" />创建 Agent</button>
      </div>
    </div>
    <NSpin :show="busy"
      ><EmptyState
        v-if="!visible.length"
        title="没有匹配的 Agent"
        description="创建不同角色的 Agent，配置其能力、模型与工具权限。"
        ><button class="button primary" @click="open()">
          <Plus :size="16" />创建 Agent
        </button></EmptyState
      >
      <div v-else class="agent-grid">
        <article v-for="agent in visible" :key="agent.id" class="panel agent-card">
          <div class="agent-card-top">
            <span class="agent-avatar"><Bot :size="24" /></span
            ><StatusBadge :state="agent.enabled ? 'enabled' : 'disabled'" />
          </div>
          <h2>
            {{ agent.name }}<span class="version">v{{ agent.version }}</span>
          </h2>
          <p class="mono muted small">{{ agent.id }}</p>
          <p class="agent-description">{{ agent.responsibilities }}</p>
          <div class="tag-list">
            <span v-for="skill in agent.skills" :key="skill">{{ skill }}</span><span
              v-if="!agent.skills.length"
              class="muted"
              >未配置技能标签</span
            >
          </div>
          <dl class="agent-meta">
            <div>
              <dt><Fingerprint :size="14" />角色</dt>
              <dd>{{ agent.role }}</dd>
            </div>
            <div>
              <dt><Cpu :size="14" />模型</dt>
              <dd>
                {{ agent.model }}<span v-if="agent.provider === 'mock'" class="mock-tag">MOCK</span
                ><span v-else class="real-tag">REAL 配置</span>
              </dd>
            </div>
            <div>
              <dt><ShieldCheck :size="14" />工具权限</dt>
              <dd>{{ agent.tools.map((t) => t.id).join(', ') || '无工具权限' }}</dd>
            </div>
          </dl>
          <div class="agent-card-actions">
            <button @click="open(agent)"><Pencil :size="14" />编辑配置</button
            ><button @click="viewProfile(agent)">能力画像<ArrowUpRight :size="14" /></button
            ><NSwitch
              :value="agent.enabled"
              :aria-label="'切换 ' + agent.name + ' 启用状态'"
              size="small"
              @update:value="toggle(agent)"
            />
          </div>
        </article></div
    ></NSpin>
    <NModal
      v-model:show="show"
      :mask-closable="false"
      @after-leave="form.system_prompt = ''"
      :auto-focus="false"
      ><section class="form-modal">
        <header>
          <div>
            <p class="eyebrow">AGENT CONFIGURATION</p>
            <h2>{{ editing ? '编辑 Agent 配置' : '创建 Agent' }}</h2>
          </div>
          <button class="icon-button" aria-label="关闭" @click="show = false">
            <X :size="20" />
          </button>
        </header>
        <form @submit.prevent="save">
          <div class="form-grid">
            <label
              >Agent ID<input
                v-model="form.id"
                required
                pattern="[A-Za-z0-9_.-]+"
                maxlength="64"
                :disabled="!!editing"
                placeholder="例如 researcher" /></label
            ><label
              >名称<input
                v-model="form.name"
                required
                maxlength="128"
                placeholder="研究分析师" /></label
            ><label
              >配置版本<input
                v-model="form.version"
                required
                pattern="[A-Za-z0-9_.-]+"
                maxlength="64" /></label
            ><label
              >角色<input
                v-model="form.role"
                required
                maxlength="2048"
                placeholder="研究 / 撰写 / 审核" /></label
            ><label class="full"
              >职责说明<textarea
                v-model="form.responsibilities"
                required
                maxlength="2048"
                rows="2"
                placeholder="描述 Agent 负责完成的任务"
              /></label
            ><label class="full"
              >System Prompt
              <span class="muted small">{{
                editing
                  ? '留空保留原私有 Prompt；不从 API 回读'
                  : '仅写入后端配置，不显示在查询响应中'
              }}</span><textarea
                v-model="form.system_prompt"
                :required="!editing"
                maxlength="8192"
                rows="4"
                placeholder="输入角色指令和执行边界"
                autocomplete="off"
              /></label
            ><label
              >知识库 ID（逗号分隔，仅绑定库可检索）<input
                v-model="form.knowledge_base_ids" /></label
            ><label
              >技能标签（逗号分隔）<input
                v-model="form.skills"
                placeholder="analysis, writing" /></label
            ><label
              >任务类型（逗号分隔）<input
                v-model="form.task_types"
                required
                placeholder="* 或 research, review" /></label
            ><label
              >Provider<select v-model="form.provider" @change="providerChanged">
                <template v-if="modelProviders.length"
                  ><option
                    v-for="provider in modelProviders"
                    :key="provider.id"
                    :value="provider.id"
                  >
                    {{ provider.id }} ·
                    {{
                      provider.mode === 'mock'
                        ? 'MOCK'
                        : provider.enabled
                          ? 'REAL 已启用'
                          : 'REAL 未启用'
                    }}
                  </option></template
                >
                <template v-else
                  ><option value="mock">Mock · 可复现模拟</option>
                  <option value="openai_compatible">OpenAI 兼容 · 需服务端授权</option></template
                >
              </select></label
            ><label
              >模型标识<input v-model="form.model" required list="model-options" /><datalist
                id="model-options"
              >
                <option v-for="model in selectedProvider?.models ?? []" :key="model.id">
                  {{ model.id }}
                </option>
              </datalist></label
            ><label
              >结构化输出模式<select v-model="form.response_format">
                <option value="text">文本（工作流可解析 JSON）</option>
                <option value="prompt_json">Prompt JSON · 无原生参数</option>
                <option
                  value="json_object"
                  :disabled="!selectedModel?.response_formats.includes('json_object')"
                >
                  原生 JSON Object · 需能力声明
                </option>
              </select></label
            ><label
              >最大并发<input
                v-model.number="form.max_concurrency"
                type="number"
                required
                min="1"
                max="16" /></label
            ><label
              >工具白名单<span class="check-label"
                ><input v-model="form.tools" type="checkbox" />安全计算器 calculator@1</span
              ></label
            ><label
              >预估相对成本<input
                v-model="form.estimated_cost"
                type="number"
                min="0"
                step="any"
                placeholder="未配置" /></label
            ><label
              >预估耗时（ms）<input
                v-model="form.estimated_latency_ms"
                type="number"
                min="0"
                step="any"
                placeholder="未配置" /></label
            ><label class="check-label"
              ><input v-model="form.enabled" type="checkbox" />启用 Agent</label
            ><label class="check-label"
              ><input v-model="form.available" type="checkbox" />可接收任务</label
            >
          </div>
          <p v-if="form.provider !== 'mock'" class="warning-note">
            此页面不接收 API Key。真实模型需服务端独立配置并授权，创建配置不会自动调用模型。
          </p>
          <div v-if="formError" class="error-banner" role="alert">{{ formError }}</div>
          <footer>
            <button class="button secondary" type="button" @click="show = false">取消</button
            ><button class="button primary" :disabled="saving">
              {{ saving ? '正在保存…' : '保存配置' }}
            </button>
          </footer>
        </form>
      </section></NModal
    >
    <NModal v-model:show="showProfile"
      ><section class="form-modal">
        <header>
          <h2>Agent 能力画像</h2>
          <button class="icon-button" @click="showProfile = false" aria-label="关闭画像">
            <X :size="20" />
          </button>
        </header>
        <div v-if="profile" class="modal-content">
          <div class="mini-stat-grid">
            <div>
              <span>执行样本</span><strong>{{ profile.stats.sample_count }}</strong>
            </div>
            <div>
              <span>经验执行成功率</span
              ><strong>{{
                profile.stats.empirical_success_rate === null
                  ? '无样本'
                  : (profile.stats.empirical_success_rate * 100).toFixed(1) + '%'
              }}</strong>
            </div>
            <div>
              <span>并发负载</span
              ><strong>{{ profile.capacity.active }} / {{ profile.capacity.capacity }}</strong>
            </div>
          </div>
          <p class="info-note">
            {{
              profile.low_samples ? '样本不足，不能据此认定高成功率。' : '画像包含实际执行统计。'
            }}执行可靠性与结果验证质量分别记录。
          </p>
          <JsonPanel :value="profile" title="人工配置与执行统计" />
        </div>
        <div v-else-if="profileError" class="modal-content">
          <p class="error-banner" role="alert">{{ profileError }}</p>
        </div>
        <EmptyState v-else title="正在读取能力画像" /></section
    ></NModal>
  </div>
</template>
