// Model output rendering fixtures, never real inference evidence.
import { createApp, h } from 'vue'
import { expect, it } from 'vitest'
import MarkdownDocument from '../src/components/MarkdownDocument.vue'
import { deliveryMarkdown, type WorkspaceTask } from '../src/workspace'
import campus from '../../backend/tests/fixtures/workspace_11b_campus_protocol_outputs.json'

it('renders Chinese headings, database tables and code as text without executable HTML', () => {
  const root = document.createElement('div')
  const source =
    '## 数据库设计\n\n| 表 | 字段 |\n|---|---|\n| Item | owner_id |\n\n- 外键关联\n```sql\nSELECT item_id FROM Item;\n```\n<script>window.bad=true</script>\n![image](https://example.invalid/a)'
  const app = createApp({ render: () => h(MarkdownDocument, { source }) })
  app.mount(root)
  expect(root.querySelector('h2')?.textContent).toBe('数据库设计')
  expect(root.querySelector('td')?.textContent).toBe('Item')
  expect(root.querySelector('code')?.textContent).toContain('SELECT item_id')
  expect(root.querySelector('script')).toBeNull()
  expect(root.querySelector('img')).toBeNull()
  expect(root.textContent).toContain('<script>')
  app.unmount()
})

it('Markdown export retains the actual document and mode instead of returning the input request', () => {
  const task = {
    simulated: false,
    delivery: {
      title: '校园设计',
      summary: '实际产物',
      items: ['摘要'],
      markdown: '## 需求分析\n用户发布物品，订单保存交易快照。',
    },
    report: { verified: true },
  } as unknown as WorkspaceTask
  const exported = deliveryMarkdown(task)
  expect(exported).toContain('## 需求分析\n用户发布物品')
  expect(exported).toContain('真实模型记录')
  expect(deliveryMarkdown({ ...task, report: { ...task.report!, verified: false } })).toBe('')
})

it('renders and exports the substantive synthetic campus document received by the workflow', () => {
  const root = document.createElement('div')
  const app = createApp({ render: () => h(MarkdownDocument, { source: campus.s3.markdown }) })
  app.mount(root)
  expect([...root.querySelectorAll('h2')].map((x) => x.textContent)).toEqual([
    '需求分析',
    '系统架构设计',
    '数据库设计',
    '技术选型',
    '方案审查',
  ])
  expect(root.textContent).toContain('price_snapshot')
  expect(root.textContent).toContain('UNIQUE(order_id, author_id)')
  expect(root.querySelectorAll('tbody tr').length).toBe(6)
  const task = {
    simulated: true,
    delivery: campus.s3,
    report: { verified: true },
  } as unknown as WorkspaceTask
  expect(deliveryMarkdown(task)).toContain(campus.s3.markdown)
  app.unmount()
})
