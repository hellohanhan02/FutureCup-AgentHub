<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import {
  Bot,
  GitBranch,
  CheckCheck,
  ShieldCheck,
  ArrowUpRight,
  Plus,
  CircleAlert,
  ArrowRight,
  Play,
} from '@lucide/vue'
import { NSpin } from 'naive-ui'
import { api } from '../api'
import type { Overview } from '../types'
import { refreshVersion, stamp } from '../composables'
import RunTable from '../components/RunTable.vue'
import EmptyState from '../components/EmptyState.vue'
import StrategyChart from '../components/StrategyChart.vue'
const data = ref<Overview>(),
  busy = ref(false),
  error = ref('')
async function load() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    data.value = await api.overview()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}
const rate = computed(() =>
  data.value?.runs.terminal
    ? Math.round((data.value.runs.succeeded / data.value.runs.terminal) * 100) + '%'
    : '—',
)
const totalVerification = computed(() =>
  Object.values(data.value?.verification ?? {}).reduce((a, b) => a + b, 0),
)
const verifiedRate = computed(() =>
  totalVerification.value && data.value
    ? Math.round((data.value.verification.PASSED / totalVerification.value) * 100) + '%'
    : '—',
)
onMounted(load)
watch(refreshVersion, load)
</script>
<template>
  <div class="dashboard">
    <div v-if="error" class="error-banner" role="alert">
      {{ error }}<button @click="load">重试</button>
    </div>
    <NSpin :show="busy">
      <div class="overview-intro">
        <div>
          <span class="tiny-chip">协作，始于编排</span>
          <h2>让复杂任务，有序发生。</h2>
          <p>连接 Agent 的能力，观察每一次调度，验证每一个结果。</p>
          <RouterLink to="/workflows" class="button primary"
            ><Plus :size="16" />创建工作流<ArrowUpRight :size="15"
          /></RouterLink>
        </div>
        <div class="intro-visual" aria-hidden="true">
          <span class="visual-line l1"></span><span class="visual-line l2"></span
          ><span class="visual-line l3"></span
          ><span class="visual-node vn1"><Bot :size="22" /><small>Agent</small></span
          ><span class="visual-node vn2"><GitBranch :size="27" /></span
          ><span class="visual-node vn3"><ShieldCheck :size="24" /><small>Verified</small></span
          ><span class="visual-spark">✦</span
          ><span class="visual-label">ORCHESTRATE WITH CONFIDENCE</span>
        </div>
      </div>
      <div class="stat-grid">
        <div class="stat-card">
          <div class="stat-top">
            <span>Agent 总数</span><span class="stat-icon green"><Bot :size="20" /></span>
          </div>
          <div class="stat-value">{{ data?.agents.total ?? '—' }}<small>个</small></div>
          <p>
            <span class="positive">{{ data?.agents.available ?? '—' }} 可用</span
            ><span> / {{ data?.agents.enabled ?? '—' }} 已启用</span>
          </p>
        </div>
        <div class="stat-card">
          <div class="stat-top">
            <span>工作流</span><span class="stat-icon blue"><GitBranch :size="20" /></span>
          </div>
          <div class="stat-value">{{ data?.workflows.total ?? '—' }}<small>个</small></div>
          <p>
            {{ data?.workflows.versions ?? '—' }} 个配置版本 <span class="muted">· 持久化存储</span>
          </p>
        </div>
        <div class="stat-card">
          <div class="stat-top">
            <span>执行成功率</span><span class="stat-icon purple"><CheckCheck :size="20" /></span>
          </div>
          <div class="stat-value">{{ rate }}</div>
          <p>
            {{ data?.runs.succeeded ?? '—' }} / {{ data?.runs.terminal ?? '—' }} 次终态执行
            <span class="muted">含取消</span>
          </p>
        </div>
        <div class="stat-card">
          <div class="stat-top">
            <span>验证通过率</span><span class="stat-icon amber"><ShieldCheck :size="20" /></span>
          </div>
          <div class="stat-value">{{ verifiedRate }}</div>
          <p>{{ data?.verification.PASSED ?? '—' }} / {{ totalVerification }} 次独立验证</p>
        </div>
      </div>
      <div class="dashboard-middle">
        <section class="panel recent-panel">
          <div class="panel-heading">
            <div>
              <span class="section-marker"></span>
              <h2>最近执行</h2>
              <span class="count-chip">{{ data?.runs.active ?? 0 }} 运行中</span>
            </div>
            <RouterLink to="/runs">查看全部<ArrowRight :size="14" /></RouterLink>
          </div>
          <RunTable :runs="data?.recent ?? []" />
        </section>
        <section class="panel strategy-panel">
          <div class="panel-heading">
            <div><h2>调度策略</h2></div>
            <span class="muted">执行分布</span>
          </div>
          <template v-if="data?.sample_size"
            ><div class="chart-wrap">
              <StrategyChart :values="data.strategies" />
              <div class="chart-center">
                <strong>{{ data.sample_size }}</strong><small>根工作流执行</small>
              </div>
            </div>
            <div class="chart-legend">
              <div v-for="(value, key, index) in data.strategies" :key="key">
                <span :class="['legend-dot', 'legend-' + index]"></span
                ><span>{{
                  key === 'adaptive_caas' ? 'Adaptive CAAS' : key === 'caas' ? 'CAAS 自适应' : key === 'fixed' ? 'Fixed 固定' : 'Round Robin'
                }}</span
                ><strong>{{ value }}</strong>
              </div>
            </div></template
          ><EmptyState v-else title="尚无调度数据" description="执行工作流后显示真实策略分布。" />
        </section>
      </div>
      <div class="dashboard-bottom">
        <section class="panel">
          <div class="panel-heading">
            <div>
              <CircleAlert :size="17" class="muted" />
              <h2>最近异常</h2>
            </div>
            <span class="muted">执行及恢复异常</span>
          </div>
          <div v-if="data?.exceptions.length" class="exception-list">
            <RouterLink v-for="item in data.exceptions" :key="item.id" :to="'/runs/' + item.id"
              ><span class="exception-icon"><CircleAlert :size="16" /></span>
              <div>
                <strong>{{ item.workflow.id }}</strong><small>{{
                  item.error_code ?? '存在节点异常，请查看详情'
                }}</small>
              </div>
              <small>{{ stamp(item.started_at) }}</small><ArrowUpRight :size="15"
            /></RouterLink>
          </div>
          <EmptyState
            v-else
            title="暂无异常记录"
            description="本窗口尚未记录执行异常；不代表任务结果已通过验证。"
          />
        </section>
        <section class="panel quick-start">
          <span class="eyebrow">QUICK START</span>
          <h2>从一个可复现的示例开始</h2>
          <p>
            顺序协作、并行汇合、验证与反馈修复。每个示例都接入真实引擎，模型使用明确标记的 Mock。
          </p>
          <RouterLink to="/workflows?demo=sequential" class="text-link"
            ><Play :size="15" />打开示范工作流<ArrowRight :size="15"
          /></RouterLink>
          <div class="quick-tags">
            <span>版本化配置</span><span>有界执行</span><span>完整审计</span>
          </div>
        </section>
      </div>
      <p class="data-note">
        统计范围：最近最多 200
        次根工作流执行及其子流程验证；无样本时不计算成功率。模拟执行在记录中标记 MOCK。
      </p>
    </NSpin>
  </div>
</template>
