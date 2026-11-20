<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
import type { DemoInfo, Json } from '../types'
import JsonPanel from '../components/JsonPanel.vue'
import StatusBadge from '../components/StatusBadge.vue'
import CaseFlow from '../components/CaseFlow.vue'
import BenchmarkChart from '../components/BenchmarkChart.vue'
import type { Metric } from '../presentation'
import { duration, stamp } from '../composables'

const info = ref<DemoInfo>(),
  error = ref(''),
  structure = ref<Json>(),
  structureError = ref('')
const metric = ref<Metric>('success_rate')
async function load() {
  error.value = ''
  try {
    const value = await api.demoInfo()
    if (value.mode !== 'historical_read_only') throw new Error('not replay')
    info.value = value
  } catch {
    error.value =
      '只读证据服务未就绪。请先导出 10-B 证据包，再启动专用回放服务；本页不会提交模型任务。'
  }
}
async function showStructure(id: string) {
  structure.value = undefined
  structureError.value = ''
  try {
    structure.value = await api.demoStructure(id)
  } catch (e) {
    structureError.value = (e as Error).message
  }
}
onMounted(load)
</script>
<template>
  <div class="demo-grid">
    <section class="panel demo-panel">
      <span class="tiny-chip">AGENT HARNESS · 真实证据驱动</span>
      <h2 class="demo-title">让协作可见，让修复有据。</h2>
      <p>
        只读回放已有 SQLite 记录的脱敏视图，不重新调用 DeepSeek，不提供执行或修复按钮。Mock
        演示请使用独立的默认关闭真实模型的服务。
      </p>
      <details class="demo-boundaries">
        <summary>只读与隐私边界 · 历史构建版本未知</summary>
        <p>
          当前 Git 版本不能倒推过去运行时版本。历史运行时构建版本：未知。原始审计仍保存在私有 SQLite
          中。
        </p>
      </details>
      <div v-if="error" class="error-banner" role="alert">
        {{ error }} <button @click="load">重试读取</button>
      </div>
      <p v-else-if="!info">正在加载只读证据…</p>
      <div class="demo-cases">
        <article v-for="run in info?.runs" :key="run.run_id" class="demo-case">
          <small class="case-eyebrow">{{
            run.execution_order.length > 1 ? '01 / 多 Agent 协作' : '02 / 独立验证与反馈'
          }}</small>
          <h3>
            {{ run.execution_order.length > 1 ? '三 Agent 串行协作' : 'Self-Loop 自适应修复' }}
            <StatusBadge :state="run.state" />
          </h3>
          <p>
            <strong>{{
              run.simulated === false
                ? '真实模型执行记录'
                : run.simulated === true
                  ? 'MOCK 模拟记录'
                  : '模型模式未知'
            }}</strong>
            · <StatusBadge :state="run.verified ? 'PASSED' : 'unverified'" />
          </p>
          <CaseFlow :run="run" />
          <div class="case-metrics">
            <div>
              <strong>{{ run.resource_usage.model_calls ?? '未知' }}</strong><small>模型调用</small>
            </div>
            <div>
              <strong>{{ run.resource_usage.reported_tokens ?? '未知' }}</strong><small
                >已报告 Tokens</small
              >
            </div>
            <div>
              <strong>{{ duration(run.elapsed_ms) }}</strong><small>历史执行耗时</small>
            </div>
            <div>
              <strong>{{ run.resource_usage.feedback_loops ?? '未知' }}</strong><small
                >修复次数</small
              >
            </div>
          </div>
          <p class="small muted">{{ stamp(run.started_at) }} · ID {{ run.run_id.slice(0, 10) }}…</p>
          <details>
            <summary>完整运行 ID 与审计定位</summary>
            <JsonPanel :value="{ run_id: run.run_id }" />
          </details>
          <p v-for="mapping in run.data_mappings" :key="mapping.from + mapping.to">
            {{ mapping.from }} → {{ mapping.to }}.{{ mapping.input_field }}：{{
              mapping.matches ? '已核对实际产物传递' : '映射未匹配'
            }}
          </p>
          <div class="section-toolbar">
            <RouterLink :to="'/runs/' + run.run_id" class="button">查看执行因果链</RouterLink
            ><button class="button secondary" @click="showStructure(run.run_id)">
              查看已保存结构与映射
            </button>
          </div>
        </article>
      </div>
      <p v-if="info && !info.runs.length">证据包中没有运行记录。</p>
      <p v-if="structureError" role="alert">{{ structureError }}</p>
      <details v-if="structure" open>
        <summary>已保存工作流定义（脱敏只读视图）</summary>
        <JsonPanel :value="structure" />
      </details>
    </section>
    <section class="panel demo-panel">
      <h2>CAAS 公平基准 · 离线模拟实验</h2>
      <p class="warning-note">
        逻辑毫秒，不是真实 LLM 耗时；未修改原 CAAS 权重。{{
          !info?.benchmark
            ? '统计摘要尚未载入，不推断显著性。'
            : info.benchmark.paired_comparisons.some((c) => c.significant_05)
              ? '当前摘要存在显著项，请核对完整统计报告。'
              : '预定 16 项比较经 Holm 校正后无显著优势。'
        }}
      </p>
      <p>
        20 个种子、四类场景、三种策略。完整原始数据及统计图见
        results/scheduler-10a；不隐藏不利场景。
      </p>
      <div v-if="info?.benchmark" class="segmented metric-switch" aria-label="基准指标切换">
        <button
          v-for="m in [
            { key: 'success_rate', name: '成功率' },
            { key: 'mean_latency_ms', name: '平均延迟' },
            { key: 'p95_latency_ms', name: 'P95 延迟' },
          ]"
          :key="m.key"
          :class="{ selected: metric === m.key }"
          @click="metric = m.key as Metric"
        >
          {{ m.name }}
        </button>
      </div>
      <BenchmarkChart v-if="info?.benchmark" :groups="info.benchmark.groups" :metric="metric" />
      <details v-if="info?.benchmark" class="raw-benchmark">
        <summary>展开全部 12 行原始数据</summary>
        <div class="demo-table">
          <table v-if="info?.benchmark">
            <thead>
              <tr>
                <th>场景</th>
                <th>策略</th>
                <th>成功率</th>
                <th>平均逻辑 ms</th>
                <th>P95 逻辑 ms</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="g in info.benchmark.groups" :key="g.scenario + g.strategy">
                <td>{{ g.scenario }}</td>
                <td>{{ g.strategy }}</td>
                <td>
                  {{
                    g.metrics.success_rate.mean == null
                      ? '未记录'
                      : (g.metrics.success_rate.mean * 100).toFixed(2) + '%'
                  }}
                </td>
                <td>{{ g.metrics.mean_latency_ms.mean?.toFixed(3) }}</td>
                <td>{{ g.metrics.p95_latency_ms.mean?.toFixed(3) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </details>
      <p v-if="info && !info.benchmark">未导入 10-A 基准数据，不生成替代数据。</p>
      <details v-if="info?.benchmark">
        <summary>查看 10-A 原始统计图（离线模拟）</summary>
        <img
          v-for="name in ['success-rate', 'mean-latency', 'p95-latency']"
          :key="name"
          :src="'/api/v1/demo-charts/' + name + '.svg'"
          :alt="name + ' 离线模拟统计图'"
        />
      </details>
    </section>
  </div>
</template>
<style scoped>
.demo-grid {
  display: grid;
  gap: 20px;
}
.demo-panel {
  padding: 24px;
  min-width: 0;
}
.demo-case {
  border-top: 1px solid #e2e8e5;
  padding: 18px 0;
}
.demo-case code {
  overflow-wrap: anywhere;
}
.demo-table {
  overflow-x: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
}
th,
td {
  padding: 10px;
  text-align: left;
  border-bottom: 1px solid #e2e8e5;
}
p {
  line-height: 1.7;
}
img {
  display: block;
  width: 100%;
  height: auto;
}
</style>
