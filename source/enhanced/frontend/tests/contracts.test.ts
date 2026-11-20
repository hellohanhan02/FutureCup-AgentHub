import { afterEach, describe, expect, it, vi } from 'vitest'
import { request, ApiError } from '../src/api'
import { blankWorkflow, demoWorkflow, initializeDemos, canonical } from '../src/demos'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})
const result = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

describe('API boundary', () => {
  it('uses only the same-origin API proxy', async () => {
    const fetch = vi.fn().mockResolvedValue(result({ status: 'ok' }))
    vi.stubGlobal('fetch', fetch)
    expect(await request('/health')).toEqual({ status: 'ok' })
    expect(fetch.mock.calls[0][0]).toBe('/api/v1/health')
  })
  it('preserves stable error codes and provides Chinese feedback', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          result({ error: { code: 'workflow_cycle', message: 'Invalid graph' } }, 422),
        ),
    )
    await expect(request('/workflows/validate')).rejects.toMatchObject({
      status: 422,
      code: 'workflow_cycle',
      message: expect.stringContaining('普通依赖图存在环'),
    })
  })
  it('reports network failure without inventing records', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network')))
    await expect(request('/agents')).rejects.toThrow('无法连接 API')
  })
  it('cancels hung requests and warns about uncertain submissions', async () => {
    vi.useFakeTimers()
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (_url, options) =>
          new Promise((_resolve, reject) =>
            options.signal.addEventListener('abort', () => reject(new Error('abort'))),
          ),
      ),
    )
    const pending = request('/workflows/example/executions', {}, 20)
    const asserted = expect(pending).rejects.toThrow('避免重复执行')
    await vi.advanceTimersByTimeAsync(21)
    await asserted
  })
})

describe('workflow contracts and deterministic Mock initialization', () => {
  it('sequential demo contains real upstream data mappings', () => {
    const flow = demoWorkflow('sequential')
    expect(flow.edges).toHaveLength(2)
    expect(flow.nodes[1].config.input).toEqual({ $ref: 'nodes.research.output' })
    expect(flow.nodes[2].config.agent_id).toBe('fc-reviewer')
  })
  it('parallel demo uses an actual join barrier', () => {
    const flow = demoWorkflow('parallel')
    expect(flow.nodes.find((n) => n.id === 'join')).toMatchObject({
      kind: 'parallel',
      config: { join: 'all' },
    })
    expect(flow.edges.filter((e) => e.target === 'join')).toHaveLength(2)
  })
  it('Self-Loop is a task configuration with bounded attempts, not a fake node', () => {
    const flow = demoWorkflow('selfloop')
    expect(flow.nodes[0]).toMatchObject({
      kind: 'task',
      config: {
        verification: { id: 'fc-demo-answer', version: '1' },
        recovery: { max_attempts: 2, max_feedback_loops: 1 },
      },
    })
    expect(flow.nodes).toHaveLength(1)
  })
  it('returns independent editable copies and excludes canvas fields', () => {
    const one = demoWorkflow('sequential')
    one.nodes[0].id = 'changed'
    expect(demoWorkflow('sequential').nodes[0].id).toBe('research')
    expect(canonical(blankWorkflow())).not.toContain('position')
    expect(canonical({ b: 2, a: 1 })).toBe(canonical({ a: 1, b: 2 }))
  })
  it('repeated initialization is idempotent and never executes models', async () => {
    const agents: unknown[] = [],
      policies: unknown[] = []
    const calls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string, init: RequestInit) => {
        calls.push(path)
        if (path === '/api/v1/agents' && init.method === 'POST') {
          agents.push({ ...JSON.parse(String(init.body)), enabled: true, available: true })
          return result(agents.at(-1), 201)
        }
        if (path === '/api/v1/verification-policies' && init.method === 'POST') {
          const value = JSON.parse(String(init.body))
          policies.push({ ...value, ref: { id: value.id, version: value.version } })
          return result(policies.at(-1), 201)
        }
        return result(path.endsWith('/agents') ? agents : policies)
      }),
    )
    await initializeDemos()
    await initializeDemos()
    expect(agents).toHaveLength(4)
    expect(policies).toHaveLength(1)
    expect(calls.some((c) => c.includes('/executions'))).toBe(false)
  })
  it('does not overwrite an existing modified demo Agent', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        result([
          {
            id: 'fc-researcher',
            provider: 'openai_compatible',
            model: 'other',
            version: '1',
            enabled: true,
            available: true,
          },
        ]),
      ),
    )
    await expect(initializeDemos()).rejects.toThrow('初始化不会覆盖')
  })
})
