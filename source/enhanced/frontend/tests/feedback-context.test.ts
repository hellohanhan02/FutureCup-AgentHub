// Synthetic audit fixtures only. No fetch, browser service, or model requests.
import { afterEach, expect, it } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import FeedbackContext from '../src/components/FeedbackContext.vue'
import type { Attempt, Verification, Feedback } from '../src/types'
let app: App | undefined
afterEach(() => {
  app?.unmount()
  document.body.innerHTML = ''
})
function fixtures() {
  const artifact = {
    ref: { id: 'artifact', version: '1' },
    digest: 'digest',
    content: { total: 9 },
  }
  const prior = { id: 'attempt1', ordinal: 1, artifact } as Attempt
  const attempt = {
    id: 'attempt2',
    ordinal: 2,
    input_value: {
      original_input: {},
      recovery: {
        previous_attempt_id: 'attempt1',
        previous_artifact: artifact,
        verification: { id: 'verification1', verdict: 'fail' },
        feedback: { id: 'feedback1', action: 'prompt_retry', category: 'ASSERTION_FAILED' },
      },
    },
  } as Attempt
  const verification = {
    record: { id: 'verification1', verdict: 'fail' },
    attempt_id: 'attempt1',
  } as Verification
  const feedback = {
    record: { id: 'feedback1' },
    attempt_id: 'attempt1',
    repair_action: 'prompt_retry',
    category: 'ASSERTION_FAILED',
    evidence_refs: ['verification1'],
  } as Feedback
  return {
    attempt,
    attempts: [prior, attempt],
    verifications: [verification],
    feedback: [feedback],
  }
}
async function render(props: ReturnType<typeof fixtures>) {
  const root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(FeedbackContext, props) })
  app.mount(root)
  await nextTick()
  return root.textContent ?? ''
}
it('shows saved input binding with artifact, verification and actual feedback references', async () => {
  const text = await render(fixtures())
  expect(text).toContain('本轮输入已包含绑定的产物与验证反馈')
  expect(text).toContain('prompt_retry')
  expect(text).toContain('不代表模型已理解或修复成功')
})
it('does not infer feedback handoff for legacy input', async () => {
  const value = fixtures()
  value.attempt.input_value = { recovery: { attempt: 2 } }
  expect(await render(value)).toContain('未确认反馈输入绑定')
})
it('rejects unrelated verification and feedback evidence', async () => {
  const value = fixtures()
  value.feedback[0].evidence_refs = ['unrelated-verification']
  expect(await render(value)).toContain('未确认反馈输入绑定')
})
it('does not show a repair receipt on the first attempt', async () => {
  const value = fixtures()
  value.attempt = value.attempts[0]
  expect(await render(value)).toBe('')
})
