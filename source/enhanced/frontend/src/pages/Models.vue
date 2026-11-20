<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { Cpu, ShieldCheck, RefreshCw, ArrowRight, LockKeyhole } from '@lucide/vue'
import { NSpin, useMessage } from 'naive-ui'
import { api } from '../api'
import type { ModelCatalog, CostEstimate } from '../types'
import { refreshVersion, stamp } from '../composables'
import { scenarioDescriptions, initializeScenarioMocks } from '../scenarios'
import EmptyState from '../components/EmptyState.vue'
import JsonPanel from '../components/JsonPanel.vue'
const catalog = ref<ModelCatalog>(),
  busy = ref(false),
  error = ref(''),
  initializing = ref(false),
  message = useMessage()
const providerId = ref('deepseek'),
  modelId = ref(''),
  inputHint = ref(1000),
  outputLimit = ref(1024),
  calls = ref(3),
  estimate = ref<CostEstimate>(),
  estimating = ref(false),
  estimateError = ref('')
const selectedProvider = computed(() =>
  catalog.value?.providers.find((p) => p.id === providerId.value),
)
const states: Record<string, string> = {
  mock_local: '本地模拟',
  disabled: '真实调用已关闭',
  unconfigured: '待服务端配置',
  missing_credentials: '服务端凭据缺失',
  ready_not_tested: '配置就绪 · 未验证连接',
  not_checked: '尚未连接',
  observed_success: '最近一次请求成功',
  protocol_simulated: '仅模拟 HTTP 协议',
  error: '最近一次请求失败',
}
async function load() {
  busy.value = true
  error.value = ''
  try {
    catalog.value = await api.modelCatalog()
    if (!modelId.value) modelId.value = selectedProvider.value?.models[0]?.id ?? ''
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}
function changeProvider() {
  modelId.value = selectedProvider.value?.models[0]?.id ?? ''
  estimate.value = undefined
}
async function calculate() {
  estimating.value = true
  estimateError.value = ''
  estimate.value = undefined
  try {
    estimate.value = await api.estimate(
      providerId.value,
      modelId.value,
      inputHint.value,
      outputLimit.value,
      calls.value,
    )
  } catch (e) {
    estimateError.value = (e as Error).message
  } finally {
    estimating.value = false
  }
}
async function initialize() {
  initializing.value = true
  try {
    await initializeScenarioMocks()
    message.success('七个 Mock Agent 与独立规则已初始化，没有执行模型请求。')
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    initializing.value = false
  }
}
const example = {
  id: 'deepseek',
  kind: 'deepseek',
  base_url: 'https://api.deepseek.com',
  key_env: 'DEEPSEEK_API_KEY',
  enabled: false,
  models: [
    {
      id: 'deepseek-flash',
      supports_tools: false,
      response_formats: ['text', 'prompt_json', 'json_object'],
      thinking_disabled: true,
      max_context_bytes: 32768,
      max_output_tokens: 1024,
    },
  ],
  timeout_seconds: 15,
  retry: { max_retries: 0, initial_delay: 0.5, max_delay: 4 },
  revision: '1',
}
onMounted(load)
watch(refreshVersion, load)
</script>
<template>
  <div>
    <div v-if="error" class="error-banner" role="alert">
      {{ error }}<button @click="load">重试</button>
    </div>
    <div class="warning-note flex-line">
      <div>
        <strong>{{
          catalog?.real_calls_enabled ? '服务端全局开关已启用' : '真实模型调用默认关闭'
        }}</strong>
        <p>本页只查询配置，不进行付费连接测试。连接状态来自最近一次真实请求或明确的模拟测试。</p>
      </div>
      <LockKeyhole :size="22" />
    </div>
    <NSpin :show="busy"
      ><div class="agent-grid">
        <article
          v-for="provider in catalog?.providers ?? []"
          :key="provider.id"
          class="panel model-provider-card"
        >
          <header>
            <span class="stat-icon green"><Cpu :size="21" /></span
            ><span :class="provider.mode === 'mock' ? 'mock-tag' : 'real-tag'">{{
              provider.mode === 'mock' ? 'MOCK' : 'REAL 配置'
            }}</span>
          </header>
          <h2>{{ provider.id }}</h2>
          <p class="small muted break-word">{{ provider.base_url ?? '本地进程内，无外部请求' }}</p>
          <div class="info-note">
            <strong>{{ states[provider.connection.state] ?? provider.connection.state }}</strong>
            <p v-if="provider.connection.error_code" class="mono small">
              {{ provider.connection.error_code }}
            </p>
            <small v-if="provider.connection.at">{{ stamp(provider.connection.at) }}</small>
          </div>
          <p v-if="provider.key_env" class="small muted">
            环境变量：<code>{{ provider.key_env }}</code
            ><br />凭据：{{
              provider.credentials_configured ? '已在服务端设置（不显示值）' : '未设置'
            }}
          </p>
          <div class="model-list">
            <div v-for="model in provider.models" :key="model.id">
              <strong class="mono">{{ model.id }}</strong><small
                >{{ model.supports_tools ? '支持工具调用（声明）' : '未启用工具能力' }} ·
                {{ model.response_formats.join(' / ') }}</small
              ><small
                >上下文字节上限 {{ model.max_context_bytes }} · 输出 Token 上限
                {{ model.max_output_tokens }}</small
              ><small
                >输入 / 输出单价（每百万 Token）：{{ model.input_price_per_million ?? '未知' }} /
                {{ model.output_price_per_million ?? '未知' }} {{ model.currency ?? '' }}</small
              >
            </div>
          </div>
          <p v-if="!provider.models.length" class="helper">
            尚未配置允许的模型标识。请在服务端添加明确的模型策略。
          </p>
          <p v-if="provider.retry" class="helper">
            超时 {{ provider.timeout_seconds }} 秒 · 最多
            {{ provider.retry.max_retries }} 次传输重试；每次重试均计调用预算。
          </p>
        </article>
      </div></NSpin
    >
    <div class="model-settings-grid">
      <section class="panel model-settings">
        <h2><ShieldCheck :size="17" />服务端安全配置</h2>
        <p>
          API Key
          只能由服务端环境变量读取；本页没有密钥输入框，也不会返回、保存或显示密钥。配置与连接成功是不同状态。
        </p>
        <ol>
          <li>
            在服务端安全设置 <code>DEEPSEEK_API_KEY</code> 或 <code>DASHSCOPE_API_KEY</code>。
          </li>
          <li>
            通过 <code>FUTURECUP_LLM_PROVIDERS_JSON</code> 设置地址、允许模型、能力和预算；不要在
            JSON 中填写密钥。
          </li>
          <li>
            获得调用授权后，才将配置 enabled 和全局 <code>FUTURECUP_ENABLE_REAL_LLM</code> 设为
            true。
          </li>
          <li>在 Agent 管理中选择 Provider、模型与输出模式，保存新版本后主动发起任务。</li>
        </ol>
        <p class="warning-note">
          未知费用显示未知。Token
          统计和配置价格估算不等于人民币实付或精确费用硬预算。自定义地址必须服务端显式允许，浏览器不能修改转发地址。
        </p>
        <JsonPanel :value="example" title="不含密钥的配置示例（默认未启用）" />
      </section>
      <section class="panel model-settings">
        <h2>调用预算估算</h2>
        <p>
          纯本地算术，不请求模型。输入 Token
          是您的预估值；缓存、推理计价、失败请求和价格变化可能造成偏差。
        </p>
        <form class="inspector-form" @submit.prevent="calculate">
          <label
            >Provider<select v-model="providerId" @change="changeProvider">
              <option
                v-for="provider in catalog?.providers.filter((p) => p.mode !== 'mock') ?? []"
                :key="provider.id"
                :value="provider.id"
              >
                {{ provider.id }}
              </option>
            </select></label
          ><label
            >模型<select v-model="modelId">
              <option
                v-for="model in selectedProvider?.models ?? []"
                :key="model.id"
                :value="model.id"
              >
                {{ model.id }}
              </option>
            </select></label
          >
          <div class="form-grid">
            <label
              >预估输入 Token<input
                v-model.number="inputHint"
                required
                type="number"
                min="0"
                max="10000000" /></label
            ><label
              >最大输出 Token<input
                v-model.number="outputLimit"
                required
                type="number"
                min="1"
                max="4096" /></label
            ><label
              >计划调用次数<input v-model.number="calls" required type="number" min="1" max="128"
            /></label>
          </div>
          <button class="button secondary" :disabled="estimating || !modelId">计算配置估算</button>
        </form>
        <p v-if="estimateError" class="error-text" role="alert">{{ estimateError }}</p>
        <div v-if="estimate" class="budget-estimate">
          <strong>{{
            estimate.estimated_cost === null
              ? '费用未知'
              : estimate.estimated_cost.toFixed(6) + ' ' + estimate.currency
          }}</strong>
          <p>配置估算 · 非账单 · 非费用硬预算</p>
        </div>
      </section>
    </div>
    <section class="panel model-settings">
      <div class="flex-line">
        <h2>面向真实任务的端到端工作流</h2>
        <button class="button secondary" :disabled="initializing" @click="initialize">
          初始化 Mock 预演依赖
        </button>
      </div>
      <p>
        同一工作流使用真实 Runtime、CAAS、验证与 Self-Loop。当前预演只注册
        mock-scenario08；真实模型需用户后续修改 Agent 版本并授权，不会自动调用。
      </p>
      <div class="scenario-grid">
        <article v-for="scenario in scenarioDescriptions" :key="scenario.key">
          <h3>{{ scenario.name }}</h3>
          <p>{{ scenario.description }}</p>
          <RouterLink :to="'/workflows?scenario=' + scenario.key" class="text-link"
            >加载任务定义<ArrowRight :size="14"
          /></RouterLink>
        </article>
      </div>
    </section>
  </div>
</template>
