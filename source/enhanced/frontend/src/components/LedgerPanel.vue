<script setup lang="ts">
import type { LedgerView } from '../platform'
import JsonPanel from './JsonPanel.vue'
import EmptyState from './EmptyState.vue'
defineProps<{ value?: LedgerView }>()
const labels: Record<string, string> = {STARTED:'已派发 · 结果待定',SUCCEEDED:'结果已保存',FAILED:'已记录失败',UNKNOWN:'UNKNOWN · 必须对账'}
</script>
<template>
  <section class="p14d-ledger">
    <h3>持久预算与工具账本</h3>
    <EmptyState v-if="!value?.available" title="本运行没有可用账本视图" :description="value?.reason ?? '未启用或旧记录未保存，不推测金额。'" />
    <template v-else>
      <p class="info-note">账户范围含源运行与恢复运行。{{ value.unit === 'estimated_micro_cny' ? '人民币估算微元' : '资源配额单位' }}，不是供应商最终账单。</p>
      <div class="p14d-metrics">
        <div><span>总调用额度</span><strong>{{ value.max_calls }}</strong></div><div><span>已确认调用</span><strong>{{ value.confirmed_calls }}</strong></div>
        <div><span>已预留调用</span><strong>{{ value.reserved_calls }}</strong></div><div class="p14d-unknown"><span>UNKNOWN 未确认</span><strong>{{ value.unknown_calls }}</strong></div>
        <div><span>剩余调用额度</span><strong>{{ value.remaining_calls }}</strong></div>
      </div>
      <dl class="p14d-budget"><dt>总单位</dt><dd>{{ value.max_units }}</dd><dt>已确认</dt><dd>{{ value.confirmed_units }}</dd><dt>已预留</dt><dd>{{ value.reserved_units }}</dd><dt>UNKNOWN占用</dt><dd>{{ value.unknown_units }}</dd><dt>可用剩余</dt><dd>{{ value.remaining_units }}</dd></dl>
      <p class="warning-note">UNKNOWN 继续占用额度，不代表免费、退款或可再次消费。人工确认不能替代外部幂等协议及账单证据。</p>
      <div class="table-scroll"><table><thead><tr><th>工具 / 节点</th><th>状态</th><th>副作用类别</th><th>幂等摘要</th><th>重放认领</th></tr></thead><tbody>
        <tr v-for="tool in value.tools ?? []" :key="String(tool.id)"><td>{{ tool.tool_name }}<small class="block muted">{{ tool.node_id }}</small></td><td>{{ labels[String(tool.state)] ?? tool.state }}</td><td>{{ tool.side_effect_class }}</td><td class="mono">{{ tool.idempotency_summary }}</td><td>{{ tool.replay_count }}<details><summary>安全引用</summary><JsonPanel :value="tool" /></details></td></tr>
      </tbody></table></div>
      <p v-if="!value.tools?.length" class="muted">没有工具操作记录。</p>
      <h4>账本内部对账诊断</h4>
      <p class="small muted">此查询未声明完整执行库存；无诊断不等于外部账单已核实。</p>
      <p v-if="!value.findings?.length" role="status">未发现账本内部异常；不代表所有业务结论正确。</p>
      <div v-for="(item,index) in value.findings ?? []" :key="index" class="warning-note"><strong>{{ item.code }}</strong><p>需要人工或外部证据核对：{{ item.action }}</p></div>
      <p v-if="value.truncated">工具列表已截断，请使用本地私有账本核查。</p>
    </template>
  </section>
</template>
<style scoped>.p14d-budget{display:grid;grid-template-columns:auto 1fr;gap:6px 20px;padding:16px;background:#f5f9f7;border-radius:10px}.p14d-budget dd{margin:0;font-variant-numeric:tabular-nums}.p14d-unknown{border:1px solid #d3a43c!important;background:#fff9e8!important}.block{display:block}</style>
