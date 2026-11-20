<script setup lang="ts">
import { ref, computed, shallowRef, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Ban, ArrowRight, GitBranch, Clock, ShieldCheck, Bot, ChevronDown, Network, X } from '@lucide/vue'
import { NSpin, NModal, useMessage, useDialog } from 'naive-ui'
import { api } from '../api'
import type { RunReport, Decision, Attempt, Verification, Feedback, Ticket } from '../types'
import type { Observation } from '../platform'
import { validateExport, recoveryReason } from '../platform'
import { duration, stamp, elapsed, terminal, refreshVersion, usePolling } from '../composables'
import StatusBadge from '../components/StatusBadge.vue'
import JsonPanel from '../components/JsonPanel.vue'
import EmptyState from '../components/EmptyState.vue'
import ModelUsage from '../components/ModelUsage.vue'
import FeedbackContext from '../components/FeedbackContext.vue'
import RunFlow from '../components/RunFlow.vue'
import ExecutionMap from '../components/ExecutionMap.vue'
import SchedulingExplanation from '../components/SchedulingExplanation.vue'
import KnowledgeCoverage from '../components/KnowledgeCoverage.vue'
import LedgerPanel from '../components/LedgerPanel.vue'
import { violationText } from '../presentation'
const route = useRoute(), router = useRouter(), message = useMessage(), dialog = useDialog()
const report = shallowRef<RunReport>(), decisions = shallowRef<Decision[]>([]), selected = ref(''),
  attempts = shallowRef<Attempt[]>([]), verifications = shallowRef<Verification[]>([]), feedback = shallowRef<Feedback[]>([]), tickets = shallowRef<Ticket[]>([]),
  loading = ref(true), auditLoading = ref(false), error = ref(''), auditError = ref(''), activeTab = ref('chain'),
  token = ref(''), actor = ref(''), manual = ref<Ticket>(), manualAction = ref<'retry' | 'abort'>('retry'), manualError = ref(''), manualBusy = ref(false)
const observation = shallowRef<Observation>(), observationError = ref(''), enhancedSupported = ref<boolean>(),
  exportBusy = ref(false), recoveryBusy = ref(false), cancelBusy = ref(false)
const node = computed(() => report.value?.nodes.find(n => n.node_id === selected.value))
const nodeDecisions = computed(() => decisions.value.filter(d => d.node_id === selected.value))
const pendingTicket = computed(() => tickets.value.find(t => t.node_id === node.value?.id && t.state === 'pending'))
let auditRevision = -1, generation = 0, epoch = 0
const pendingRuns = new Map<string, Promise<void>>(), pendingAudits = new Map<string, Promise<void>>(), recoveryKeys = new Map<string,string>()
function loadAudit(): Promise<void> {
  const current = node.value, runId = report.value?.run.id, capturedEpoch = epoch
  if (!current || !runId) return Promise.resolve()
  const key = `${capturedEpoch}:${runId}:${current.id}:${report.value?.run.revision}`
  if (pendingAudits.has(key)) return pendingAudits.get(key)!
  const version = ++generation
  const fresh = () => epoch === capturedEpoch && generation === version && node.value?.id === current.id && report.value?.run.id === runId
  auditLoading.value = true; auditError.value = ''
  const work = (async () => {
    try {
      if (!current.attempt_ids.length) { if (fresh()) { attempts.value=[]; verifications.value=[]; feedback.value=[] }; return }
      const result = await Promise.all([api.attempts(current.id),api.verifications(current.id),api.feedback(current.id)])
      if (fresh()) [attempts.value,verifications.value,feedback.value] = result
    } catch(e) { if (fresh()) auditError.value=(e as Error).message }
    finally { if (fresh()) auditLoading.value=false }
  })()
  pendingAudits.set(key,work); void work.finally(() => pendingAudits.delete(key)); return work
}
function load(): Promise<void> {
  const id=String(route.params.id), capturedEpoch=epoch, key=`${epoch}:${id}`
  if (pendingRuns.has(key)) return pendingRuns.get(key)!
  const fresh=() => capturedEpoch===epoch && id===String(route.params.id)
  const work=(async () => {
    try {
      const [value,d,t,enhanced] = await Promise.all([api.run(id),api.decisions(id),api.tickets(),api.observation(id).catch(e => { if (fresh()) enhancedSupported.value = (e as {status?:number}).status !== 404; return null })])
      if (!fresh()) return
      if (value.run.id!==id) throw new Error('运行记录ID不一致，已拒绝显示')
      const next=selected.value && value.nodes.some(n=>n.node_id===selected.value) ? selected.value : value.nodes[0]?.node_id ?? ''
      const changed=next!==selected.value
      report.value=value; decisions.value=d; tickets.value=t; selected.value=next; error.value=''
      if (enhanced?.schema_version==='platform-14d-v1' && enhanced.run_id===id && enhanced.revision===value.run.revision) {
        observation.value=enhanced; enhancedSupported.value=true; observationError.value=''
      } else { observation.value=undefined; enhancedSupported.value=enhanced ? enhanced.schema_version==='platform-14d-v1' : enhancedSupported.value; observationError.value='增强证据未记录或版本正在更新；原运行与审计查询继续可用。' }
      if (auditRevision!==value.run.revision) { auditRevision=value.run.revision; if (!changed) await loadAudit() }
    } catch(e) { if (fresh()) error.value=(e as Error).message }
    finally { if (fresh()) loading.value=false }
  })()
  pendingRuns.set(key,work); void work.finally(()=>pendingRuns.delete(key)); return work
}
const polling = usePolling(async () => { if (!report.value || !terminal(report.value.run.state) || (enhancedSupported.value && !observation.value)) await load() })
onMounted(polling.start)
watch(()=>route.params.id,()=>{
  epoch++; generation++; report.value=undefined; observation.value=undefined; enhancedSupported.value=undefined
  selected.value=''; attempts.value=[]; verifications.value=[]; feedback.value=[]; decisions.value=[]; tickets.value=[]
  manual.value=undefined; manualBusy.value=false; manualError.value=''; token.value=''; error.value=''; auditError.value=''; observationError.value=''; auditRevision=-1
  exportBusy.value=false; recoveryBusy.value=false; cancelBusy.value=false; auditLoading.value=false; loading.value=true
  void load()
})
watch(selected,()=>{ generation++; attempts.value=[]; verifications.value=[]; feedback.value=[]; void loadAudit() })
watch(refreshVersion,load)
onBeforeUnmount(()=>{epoch++;generation++;token.value=''})
function cancel() {
  const id=report.value?.run.id, version=epoch
  if (!id || cancelBusy.value) return
  dialog.warning({title:'取消工作流执行',content:'取消传播到模型、工具和子工作流，已保存证据保留。',positiveText:'确认取消',negativeText:'继续执行',
    onPositiveClick:async()=>{
      if(version!==epoch || cancelBusy.value)return
      cancelBusy.value=true
      try {await api.cancel(id);if(version===epoch){await load();message.success('执行已取消')}}
      catch(e){if(version===epoch)message.error((e as Error).message)} finally{if(version===epoch)cancelBusy.value=false}
    }})
}
function verification(attempt: Attempt) {return verifications.value.find(v=>v.attempt_id===attempt.id)}
function repair(attempt: Attempt) {return feedback.value.find(f=>f.attempt_id===attempt.id)}
async function resolve() {
  const ticket=manual.value, version=epoch
  if (!ticket || manualBusy.value || ticket.node_id!==node.value?.id) return
  manualBusy.value=true;manualError.value=''
  try {await api.resolve(ticket,manualAction.value,actor.value,token.value);if(version===epoch){token.value='';manual.value=undefined;await load();message.success('人工决策已提交')}}
  catch(e){if(version===epoch)manualError.value=(e as Error).message} finally{if(version===epoch)manualBusy.value=false}
}
function recover() {
  const value=report.value, evidence=observation.value, version=epoch
  if (!value || !evidence?.recovery.requestable || value.simulated!==true || recoveryBusy.value || value.replay?.read_only) return
  dialog.warning({title:'申请一次 Mock 局部恢复',content:'创建全新Run ID，继承原预算和期限。仅复用证明仍有效的节点；工具安全最终由后端校验。不会强制重放副作用或开启真实模型。',positiveText:'确认申请恢复',negativeText:'取消',
    onPositiveClick:async()=>{
      if(version!==epoch || recoveryBusy.value)return
      recoveryBusy.value=true
      const key=`${value.run.id}:${value.run.revision}`
      if(!recoveryKeys.has(key))recoveryKeys.set(key,crypto.randomUUID())
      try {const result=await api.recover(value.run.id,value.run.revision,recoveryKeys.get(key)!);if(version===epoch)await router.push('/runs/'+result.run.id)}
      catch(e){if(version===epoch){const reason=(e as Error).message;await load();if(version===epoch)error.value=reason}}
      finally{if(version===epoch)recoveryBusy.value=false}
    }})
}
async function exportReport() {
  const id=report.value?.run.id, revision=report.value?.run.revision, version=epoch
  if(!id || revision==null || exportBusy.value)return
  exportBusy.value=true
  try {
    const value=await api.exportReport(id,revision)
    if(version!==epoch || id!==String(route.params.id))throw new Error('运行已切换，请在当前运行重新导出')
    validateExport(value,id,revision)
    const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json;charset=utf-8'}))
    const link=document.createElement('a');link.href=url;link.download=id+'-evidence.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
    message.success('当前运行脱敏证据已导出，公开前仍需人工复核')
  } catch(e){if(version===epoch)message.error((e as Error).message)} finally{if(version===epoch)exportBusy.value=false}
}
</script>
<template>
  <div>
    <div class="section-toolbar">
      <RouterLink to="/runs" class="text-link muted"
        ><ArrowLeft :size="16" />返回运行监控</RouterLink
      ><span class="poll-label"
        ><i></i
        >{{ report && terminal(report.run.state) ? '已完成 · 保留终态快照' : '3 秒轮询更新' }}</span
      >
    </div>
    <div v-if="error" class="error-banner" role="alert">
      {{ error }}<button @click="load">重试</button>
    </div>
    <NSpin :show="loading"
      ><template v-if="report"
        ><p v-if="report.replay?.read_only" class="warning-note" role="status">
          历史记录只读回放 · 已脱敏 · 不重新调用模型 · 运行时构建版本未知
        </p>
        <section class="panel run-overview">
          <div class="run-overview-top">
            <div class="run-title">
              <span class="stat-icon green"><GitBranch :size="24" /></span>
              <div>
                <h2>
                  {{ report.run.definition_ref.id }}
                  <span class="version">v{{ report.run.definition_ref.version }}</span
                  ><span v-if="report.simulated" class="mock-tag">MOCK 模拟执行</span>
                  <span v-else-if="report.simulated === false" class="tiny-chip"
                    >真实模型执行记录</span
                  >
                  <span v-else class="tiny-chip">模型模式未知</span>
                </h2>
                <span class="mono muted small">{{ report.run.id }}</span>
              </div>
            </div>
            <div class="toolbar-right">
              <button class="button secondary" @click="load">刷新状态</button>
              <button class="button secondary" :disabled="!observation || exportBusy" @click="exportReport">{{ exportBusy ? '正在校验…' : '导出脱敏运行报告' }}</button>
              <StatusBadge :state="report.run.state" /><button
                v-if="!report.replay?.read_only && !terminal(report.run.state)"
                class="button danger-button"
                @click="cancel"
              >
                <Ban :size="15" />取消执行
              </button>
            </div>
          </div>
          <div class="run-metrics">
            <div>
              <span>执行耗时</span
              ><strong>{{
                duration(elapsed(report.run.state, report.started_at, report.elapsed_ms))
              }}</strong>
            </div>
            <div>
              <span>节点数量</span><strong>{{ report.nodes.length }}</strong>
            </div>
            <div>
              <span>结果可信度</span
              ><StatusBadge :state="report.verified ? 'PASSED' : 'unverified'" />
            </div>
            <div>
              <span>模型 / 工具调用</span
              ><strong
                >{{ report.resource_usage.model_calls ?? 0 }} /
                {{ report.resource_usage.tool_calls ?? 0 }}</strong
              >
            </div>
            <div>
              <span>开始时间</span><strong class="small">{{ stamp(report.started_at) }}</strong>
            </div>
            <div>
              <span>记录 Token / 修复次数</span
              ><strong
                >{{ report.resource_usage.reported_tokens ?? '未知' }} /
                {{ report.resource_usage.feedback_loops ?? 0 }}</strong
              >
            </div>
          </div>
          <p v-if="report.error_code" class="error-text">终止原因：{{ report.error_code }}</p>
          <RouterLink
            v-if="report.run.parent_run_id"
            :to="'/runs/' + report.run.parent_run_id"
            class="text-link"
            >进入父工作流<ArrowRight :size="14"
          /></RouterLink>
        </section>
        <ExecutionMap v-if="observation" :value="observation" :selected="selected" @select="selected = $event" />
        <RunFlow v-else :report="report" @select="selected = $event" />
        <p v-if="observationError" class="info-note">{{ observationError }}</p>
        <section v-if="observation" class="panel p14d-recovery-panel">
          <h3>精准恢复与运行关联</h3>
          <div class="toolbar-right">
            <RouterLink v-if="observation.links.source_run_id" class="text-link" :to="'/runs/'+observation.links.source_run_id">原始运行 {{ observation.links.source_run_id.slice(0,12) }} →</RouterLink>
            <RouterLink v-if="observation.links.recovery_run_id" class="text-link" :to="'/runs/'+observation.links.recovery_run_id">恢复运行 {{ observation.links.recovery_run_id.slice(0,12) }} →</RouterLink>
            <button v-if="!report.replay?.read_only" class="button secondary" :disabled="!observation.recovery.requestable || recoveryBusy || report.simulated !== true" @click="recover">{{ recoveryBusy ? '恢复申请中…' : '申请 Mock 局部恢复' }}</button>
          </div>
          <p v-for="reason in observation.recovery.reasons" :key="reason" class="small muted">{{ recoveryReason[reason] ?? reason }}</p>
          <p class="small muted">预检不等于授权；实际权限、预算、期限与工具重放安全仍由后端校验。真实模型跨运行恢复关闭。</p>
          <details><summary>保留、失效与重执行记录 / 修复上限</summary><JsonPanel :value="{ recovery: report.recovery_audit, node_limits: observation.node_configs }" /></details>
        </section>
        <div class="run-detail-grid">
          <aside class="panel node-list">
            <div class="panel-heading">
              <div><h2>执行节点</h2></div>
            </div>
            <button
              v-for="item in report.nodes"
              :key="item.id"
              :class="{ active: selected === item.node_id }"
              @click="selected = item.node_id"
            >
              <span class="node-list-icon"><Bot :size="17" /></span>
              <div>
                <strong>{{ item.node_id }}</strong><small>{{
                  item.agent_ref?.id ?? '控制节点 / 待分配'
                }}</small
                ><StatusBadge :state="item.state" />
              </div>
              <span class="small muted">{{
                duration(elapsed(item.state, item.started_at, item.elapsed_ms))
              }}</span>
            </button>
          </aside>
          <section class="panel trace-panel">
            <div class="detail-tabs">
              <button
                v-for="item in [
                  { key: 'chain', name: '执行因果链' },
                  { key: 'scheduling', name: '调度决策' },
                  { key: 'knowledge', name: '知识覆盖' },
                  { key: 'ledger', name: '预算与工具账本' },
                  { key: 'logs', name: '事件日志' },
                  { key: 'output', name: '输入与最终结果' },
                ]"
                :key="item.key"
                :class="{ active: activeTab === item.key }"
                @click="activeTab = item.key"
              >
                {{ item.name }}
              </button>
            </div>
            <div v-if="node" class="trace-body">
              <p v-if="node.reused_from" class="warning-note" role="status">
                复用已验证结果，本次未重新执行该节点。
                <RouterLink :to="'/runs/' + node.reused_from.run_id">查看原始执行与验证证据</RouterLink>
              </p>
              <details v-if="report?.recovery_audit?.source_run"><summary>局部恢复审计：保留、失效与实际重执行</summary><JsonPanel :value="report.recovery_audit" /></details>
              <details v-if="node.repair_diagnosis"><summary>精准错误归因与允许的修复策略</summary><JsonPanel :value="node.repair_diagnosis" /></details>

              <div class="trace-node-heading">
                <h3>{{ node.node_id }}</h3>
                <StatusBadge :state="node.state" /><StatusBadge
                  :state="node.verified ? 'PASSED' : 'unverified'"
                />
              </div>
              <ModelUsage :value="node" title="节点累计用量 · 最近执行模型" />
              <div v-if="pendingTicket" class="warning-note">
                <strong>等待人工授权</strong>
                <p>整体超时继续计时。仅允许 retry / abort，不能强制验证通过。</p>
                <button
                  class="button secondary"
                  @click="
                    () => {
                      manual = pendingTicket
                      manualAction = 'retry'
                      manualError = ''
                    }
                  "
                >
                  处理人工请求
                </button>
              </div>
              <p v-if="node.error_code" class="error-text">节点错误：{{ node.error_code }}</p>
              <div v-if="auditError" class="error-banner" role="alert">
                审计查询失败：{{ auditError }}<button @click="loadAudit">重试</button>
              </div>
              <NSpin v-if="activeTab === 'chain'" :show="auditLoading"
                ><div class="cause-summary">
                  <span>任务输入</span><ArrowRight :size="14" /><span>Agent 选择</span
                  ><ArrowRight :size="14" /><span>执行产物</span><ArrowRight :size="14" /><span
                    >验证与反馈</span
                  >
                </div>
                <div v-if="attempts.length" class="attempt-timeline">
                  <article v-for="attempt in attempts" :key="attempt.id" class="attempt-card">
                    <div class="attempt-marker">{{ attempt.ordinal }}</div>
                      <details v-if="attempt.retrieval_records?.length"><summary>本轮实际知识检索与来源快照</summary><JsonPanel :value="attempt.retrieval_records" /></details>
                    <header>
                      <div>
                        <strong>尝试 {{ attempt.ordinal }}</strong
                        ><span class="mock-tag" v-if="attempt.simulated">MOCK</span
                        ><small class="mono muted">{{ attempt.id.slice(0, 12) }}</small>
                      </div>
                      <StatusBadge
                        v-if="verification(attempt)"
                        :state="verification(attempt)!.status"
                      /><StatusBadge v-else state="unverified" />
                    </header>
                    <div class="attempt-meta">
                      <span
                        >Agent：<strong
                          >{{ attempt.agent_ref?.id ?? '未分配' }} @
                          {{ attempt.agent_ref?.version ?? '—' }}</strong
                        ></span
                      ><span
                        >模型：{{ attempt.provider ?? '未调用' }} / {{ attempt.model ?? '—' }}</span
                      ><span>{{ stamp(attempt.started_at) }}</span>
                    </div>
                    <ModelUsage :value="attempt" title="本次尝试模型与用量" />
                    <FeedbackContext
                      :attempt="attempt"
                      :attempts="attempts"
                      :verifications="verifications"
                      :feedback="feedback"
                    />
                    <details>
                      <summary>
                        输入版本与任务输入
                        <span class="mono">v{{ attempt.input_ref.version }}</span>
                      </summary>
                      <JsonPanel
                        :value="{ input: attempt.input_value, digest: attempt.input_digest }"
                      />
                    </details>
                    <details open>
                      <summary>
                        执行产物 · 模型生成内容
                        <span class="muted">{{
                          verification(attempt)?.status === 'PASSED'
                            ? '通过独立验证'
                            : '隔离产物 · 不作为可信输出'
                        }}</span>
                      </summary>
                      <JsonPanel :value="attempt.artifact?.content ?? null" />
                      <p class="mono small muted break-word">
                        产物摘要：{{ attempt.artifact?.digest ?? '无产物' }}
                      </p>
                    </details>
                    <section v-if="verification(attempt)" class="evidence-box">
                      <h4><ShieldCheck :size="15" />独立验证依据 · 程序判定</h4>
                      <div
                        v-for="(e, i) in verification(attempt)!.evidence"
                        :key="i"
                        class="evidence-row"
                      >
                        <StatusBadge :state="e.status" /><span class="mono">{{ e.kind }}</span
                        ><span>{{ e.reason }}</span
                        ><small v-if="e.path">路径：{{ e.path }}</small>
                        <small v-if="e.reason === 'order09c_not_evaluated_precondition'"
                          >未评估（前置条件不满足）</small
                        >
                        <small v-if="e.rule">失败规则：{{ e.rule }}</small>
                        <small v-if="e.schema_path">规则路径：{{ e.schema_path }}</small>
                        <p
                          v-for="text in violationText(e)"
                          :key="text"
                          class="constraint-explanation"
                        >
                          {{ text }}
                        </p>
                        <details v-if="e.details">
                          <summary>约束明细</summary>
                          <JsonPanel :value="e.details" />
                        </details>
                      </div>
                      <small class="muted"
                        >来源 {{ verification(attempt)!.origin }} ·
                        {{ duration(verification(attempt)!.elapsed_ms) }} · 规则
                        {{ verification(attempt)!.record.verifier_ref.id }} @
                        {{ verification(attempt)!.record.verifier_ref.version }}</small
                      >
                    </section>
                    <div v-if="repair(attempt)" class="feedback-box">
                      <strong>反馈与恢复 · 修复建议</strong>
                      <p>
                        <span class="code-chip">{{ repair(attempt)!.category }}</span>
                        {{ repair(attempt)!.repair_action }}
                      </p>
                      <p class="small mono">
                        反馈 {{ repair(attempt)!.record.id }} ·
                        {{ stamp(repair(attempt)!.record.created_at ?? null) }}
                      </p>
                      <small
                        >故障来源：{{ repair(attempt)!.origin }} · 实际预算消耗：{{
                          repair(attempt)!.actual_budget_cost
                        }}</small
                      >
                    </div>
                    <details>
                      <summary>调度决策、版本与资源</summary>
                      <JsonPanel
                        :value="{
                          agent: attempt.agent_ref,
                          decision_id: attempt.decision_id,
                          artifact_ref: attempt.artifact?.ref,
                          policy_digest: verification(attempt)?.policy_digest,
                          resources: attempt.resources,
                          model_calls: attempt.model_calls ?? [],
                          error: attempt.error_code,
                        }"
                      />
                    </details>
                  </article>
                </div>
                <div v-else>
                  <p class="info-note">
                    {{
                      node.attempt_ids.length
                        ? '审计记录读取中或查询失败。'
                        : '此节点未启用 Self-Loop，或尚未开始尝试；没有虚构的验证记录。'
                    }}
                  </p>
                  <JsonPanel
                    :value="{
                      input: node.input,
                      agent: node.agent_ref,
                      execution_id: node.execution_id,
                      output: node.output,
                      verified: node.verified,
                    }"
                    title="节点执行记录"
                  />
                </div>
                <div v-if="node.child_run_ids.length" class="child-links">
                  <strong>子工作流执行</strong
                  ><RouterLink v-for="id in node.child_run_ids" :key="id" :to="'/runs/' + id"
                    >{{ id.slice(0, 14) }}<ArrowRight :size="14"
                  /></RouterLink></div
              ></NSpin>
              <SchedulingExplanation v-if="activeTab === 'scheduling'" :decisions="nodeDecisions" />
              <div v-if="activeTab === 'knowledge'">
                <p v-if="!attempts.length">本节点未记录尝试检索；复用节点请查看原始执行。</p>
                <section v-for="a in attempts" :key="a.id"><h3>尝试 {{ a.ordinal }} · {{ a.id.slice(0,12) }}</h3><KnowledgeCoverage :records="a.retrieval_records ?? []" :artifact="a.artifact?.content" :evidence="verification(a)?.evidence ?? []" /></section>
              </div>
              <LedgerPanel v-if="activeTab === 'ledger'" :value="observation?.ledger" />
              <div v-if="activeTab === 'logs'" class="event-log">
                <div
                  v-for="event in report.events.filter(
                    (e) => e.payload.node_id === selected || e.type.startsWith('workflow.'),
                  )"
                  :key="event.id"
                >
                  <span class="mono muted">#{{ event.sequence }}</span
                  ><time>{{ stamp(event.occurred_at) }}</time
                  ><strong>{{ event.type }}</strong
                  ><code>{{ event.payload.error_code ?? event.payload.state ?? '' }}</code>
                </div>
              </div>
              <div v-if="activeTab === 'output'">
                <p class="info-note">
                  最终结果取自工作流报告；未通过的尝试产物只保留在隔离审计记录中。
                </p>
                <JsonPanel :value="report.input" title="工作流输入" /><JsonPanel
                  :value="report.output"
                  title="工作流最终结果"
                /><JsonPanel :value="report.resource_usage" title="根工作流共享资源消耗" />
                <details>
                  <summary>完整审计摘要（当前节点 · API 脱敏视图）</summary>
                  <JsonPanel :value="{ report, attempts, verifications, feedback, decisions }" />
                </details>
              </div>
            </div>
          </section></div></template
      ><EmptyState
        v-else-if="!loading"
        title="无法读取执行记录"
        description="请检查执行 ID、后端连接或页面错误提示。"
    /></NSpin>
    <NModal
      :show="!!manual"
      :mask-closable="false"
      @update:show="
        (value) => {
          if (!value) {
            manual = undefined
            token = ''
          }
        }
      "
      ><section class="form-modal">
        <header>
          <h2>人工恢复授权</h2>
          <button
            class="icon-button"
            aria-label="关闭人工处理"
            @click="
              () => {
                manual = undefined
                token = ''
              }
            "
          >
            <X :size="20" />
          </button>
        </header>
        <form @submit.prevent="resolve">
          <div class="modal-content">
            <p class="warning-note">
              令牌仅在本次请求中发送至本地后端，不写入
              URL、浏览器存储或日志。必须由服务端配置；不能修改验证标准。
            </p>
            <label
              >决策<select v-model="manualAction">
                <option value="retry">允许在现有预算内重新尝试</option>
                <option value="abort">终止执行</option>
              </select></label
            ><label
              >操作人<input v-model="actor" required maxlength="64" autocomplete="off" /></label
            ><label
              >人工授权令牌<input v-model="token" type="password" required autocomplete="off"
            /></label>
            <p v-if="manualError" class="error-text" role="alert">{{ manualError }}</p>
          </div>
          <footer>
            <button class="button primary" :disabled="manualBusy">提交当前产物的授权决策</button>
          </footer>
        </form>
      </section></NModal
    >
  </div>
</template>
