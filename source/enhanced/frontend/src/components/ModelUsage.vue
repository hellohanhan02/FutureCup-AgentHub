<script setup lang="ts">
defineProps<{
  value: {
    provider?: string | null
    model?: string | null
    resolved_model?: string | null
    input_tokens?: number
    output_tokens?: number
    usage_complete?: boolean | null
    estimated_cost?: number | null
    cost_currency?: string | null
    cost_complete?: boolean
  }
  title?: string
}>()
</script>
<template>
  <section class="model-usage">
    <header>{{ title ?? '模型与用量' }}</header>
    <div>
      <span>Provider / 请求模型</span
      ><strong class="mono"
        >{{ value.provider ?? '未记录' }} / {{ value.model ?? '未记录' }}</strong
      >
    </div>
    <div>
      <span>服务端返回模型标识</span
      ><strong class="mono">{{ value.resolved_model ?? '未返回，不推断具体版本' }}</strong>
    </div>
    <div>
      <span>API 已报告 Token</span
      ><strong
        >输入 {{ value.input_tokens ?? 0 }} / 输出 {{ value.output_tokens ?? 0 }}
        <small :class="value.usage_complete ? 'positive' : 'muted'">{{
          value.usage_complete ? '用量报告完整' : '未知 / 不完整；不等同于零消耗'
        }}</small></strong
      >
    </div>
    <div>
      <span>配置价格估算</span
      ><strong
        >{{
          value.cost_complete && value.estimated_cost != null
            ? value.estimated_cost.toFixed(6) + ' ' + value.cost_currency
            : '未知'
        }}<small class="muted">非实付账单，非费用硬预算</small></strong
      >
    </div>
  </section>
</template>
