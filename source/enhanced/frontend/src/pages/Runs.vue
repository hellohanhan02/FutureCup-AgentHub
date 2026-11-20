<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { Search, Activity, ArrowRight } from '@lucide/vue'
import { NSpin } from 'naive-ui'
import { api, request } from '../api'
import { useRouter } from 'vue-router'
import type { RunSummary } from '../types'
import { refreshVersion, usePolling } from '../composables'
import RunTable from '../components/RunTable.vue'
const router=useRouter(), demoAvailable=ref(false), demoBusy=ref(false)
let demoKey=''
onMounted(async()=>{try{demoAvailable.value=(await request<{available?:boolean}>('/platform/demo-capabilities')).available===true}catch{demoAvailable.value=false}})
async function runDemo(){
  if(demoBusy.value || !demoAvailable.value)return
  demoBusy.value=true
  if(!demoKey)demoKey=crypto.randomUUID()
  try{const value=await request<{run:{id:string}}>('/platform/mock-demo',{method:'POST',body:JSON.stringify({request_id:demoKey,authorize_mock:true})});await router.push('/runs/'+value.run.id)}
  catch(e){error.value=(e as Error).message}
  finally{demoBusy.value=false}
}
const runs = ref<RunSummary[]>([]),
  loading = ref(true),
  error = ref(''),
  query = ref(''),
  filter = ref('all')
const filtered = computed(() =>
  runs.value.filter(
    (r) =>
      (filter.value === 'all' || r.state === filter.value) &&
      (r.id + ' ' + r.workflow.id).toLowerCase().includes(query.value.toLowerCase()),
  ),
)
const summary = computed(() => ({
  total: runs.value.length,
  succeeded: runs.value.filter((r) => r.state === 'succeeded').length,
  verified: runs.value.filter((r) => r.verified).length,
  real: runs.value.filter((r) => r.simulated === false).length,
}))
async function load() {
  try {
    runs.value = await api.runs()
    error.value = ''
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}
const polling = usePolling(load)
onMounted(polling.start)
watch(refreshVersion, load)
</script>
<template>
  <div>
    <div class="monitor-summary">
      <div>
        <small>当前记录范围</small><strong>{{ loading || error ? '—' : summary.total }}</strong>
      </div>
      <div>
        <small>执行成功</small><strong>{{ loading || error ? '—' : summary.succeeded }}</strong>
      </div>
      <div>
        <small>独立验证通过</small><strong>{{ loading || error ? '—' : summary.verified }}</strong>
      </div>
      <div>
        <small>真实模型记录</small><strong>{{ loading || error ? '—' : summary.real }}</strong>
      </div>
    </div>
    <p class="small muted">仅根据当前 API 返回记录计算，非全平台累计；不补造历史样本。</p>
    <div v-if="error" class="error-banner" role="alert">
      {{ error }}<button @click="load">重试</button>
    </div>
    <div class="section-toolbar">
      <button v-if="demoAvailable" class="button primary" :disabled="demoBusy" @click="runDemo">{{ demoBusy ? 'Mock...' : '综合 Mock 验收案例' }}</button>
      <div class="segmented">
        <button
          v-for="item in [
            { value: 'all', name: '全部执行' },
            { value: 'running', name: '执行中' },
            { value: 'succeeded', name: '已成功' },
            { value: 'failed', name: '失败' },
            { value: 'paused', name: '人工等待' },
          ]"
          :key="item.value"
          :class="{ selected: filter === item.value }"
          @click="filter = item.value"
        >
          {{ item.name }}
        </button>
      </div>
      <div class="search-input">
        <Search :size="16" /><input
          v-model="query"
          aria-label="搜索执行记录"
          placeholder="搜索执行 ID 或工作流 ID"
        />
      </div>
    </div>
    <section class="panel monitor-panel">
      <div class="panel-heading">
        <div>
          <Activity :size="17" class="positive" />
          <h2>工作流执行记录</h2>
          <span class="count-chip">{{ filtered.length }}</span>
        </div>
        <span class="poll-label"><i></i>3 秒轮询 · 最近 100 次根执行</span>
      </div>
      <NSpin :show="loading"><RunTable :runs="filtered" /></NSpin>
    </section>
    <div class="info-note flex-line">
      列表包含根工作流，子工作流可在执行详情中进入。执行成功与验证通过分别展示。<RouterLink
        to="/workflows"
        class="text-link"
        >前往编排器<ArrowRight :size="14"
      /></RouterLink>
    </div>
  </div>
</template>
