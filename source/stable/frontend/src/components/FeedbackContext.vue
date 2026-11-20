<script setup lang="ts">
import { computed } from 'vue'
import type { Attempt, Verification, Feedback, Json } from '../types'
const props = defineProps<{
  attempt: Attempt
  attempts: Attempt[]
  verifications: Verification[]
  feedback: Feedback[]
}>()
function object(value: Json | undefined): Record<string, Json> | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value : undefined
}
const binding = computed(() => {
  const recovery = object(object(props.attempt.input_value)?.recovery)
  const artifact = object(recovery?.previous_artifact)
  const reference = object(artifact?.ref)
  const check = object(recovery?.verification)
  const repair = object(recovery?.feedback)
  const prior = props.attempts.find((a) => a.id === recovery?.previous_attempt_id)
  const verification = props.verifications.find(
    (v) => v.record.id === check?.id && v.attempt_id === prior?.id,
  )
  const feedback = props.feedback.find(
    (f) => f.record.id === repair?.id && f.attempt_id === prior?.id,
  )
  const bound = Boolean(
    prior?.artifact &&
    prior.ordinal < props.attempt.ordinal &&
    prior.artifact.digest === artifact?.digest &&
    prior.artifact.ref.id === reference?.id &&
    prior.artifact.ref.version === reference?.version &&
    verification &&
    verification.record.verdict === check?.verdict &&
    feedback?.repair_action === repair?.action &&
    feedback?.category === repair?.category &&
    feedback?.evidence_refs?.includes(verification.record.id),
  )
  return { bound, prior, verification, feedback }
})
</script>
<template>
  <section v-if="attempt.ordinal > 1" class="evidence-box" aria-label="反馈输入绑定">
    <strong>{{
      binding.bound ? '本轮输入已包含绑定的产物与验证反馈' : '未确认反馈输入绑定'
    }}</strong>
    <p v-if="binding.bound" class="small muted">
      来源尝试 {{ binding.prior?.ordinal }} · 产物版本 {{ binding.prior?.artifact?.ref.version }} ·
      修复策略 {{ binding.feedback?.repair_action }}
    </p>
    <p class="small muted">
      {{
        binding.bound
          ? '依据已保存的本轮输入、上一轮产物摘要、验证和反馈引用；不代表模型已理解或修复成功。'
          : '旧记录或缺少匹配证据时不推断已传递，请展开输入版本查看。'
      }}
    </p>
  </section>
</template>
