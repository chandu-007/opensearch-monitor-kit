import { describe, expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'

const PLUGIN = 'opensearch-monitor-band'
const OSGET = '/opt/osmon/osget'
const NOT_CONFIGURED = 'opensearch-monitor-band: set osget_path in /config'
const SURFACES = ['terminal', 'desktop'] as const
// What the engine's own band draws in these tests: the plugin passed (hidden).
const ENGINE = 'engine band'
// 2026-10-10 12:04 UTC
const START = Date.UTC(2026, 9, 10, 12, 4)

const BAND = {
  component: 'AbovePrompt',
  props: {
    hasSurvey: false,
    isWorking: false,
    maxRows: 10,
    bodyColumns: 160,
    scroll: { offset: 0, bodyRows: 9 },
    view: {},
  },
} as const

const SESSION = { cwd: '/work', surface: 'terminal', isInteractive: true } as const

type Reply = { exitCode: number; stdout?: string; stderr?: string }
type Replies = { health: Reply; nodes: Reply; pools: Reply }

const ok = (body: unknown): Reply => ({ exitCode: 0, stdout: JSON.stringify(body) })

const health = (status: string, unassigned = 0) => ok({ cluster_name: 'acme', status, unassigned_shards: unassigned })

const nodes = (cpu = '62', heap = '71') =>
  ok([
    { name: 'node-alpha-01', 'node.role': 'dimr', cpu, 'heap.percent': heap },
    { name: 'node-b', 'node.role': 'dimr', cpu: '20', 'heap.percent': '40' },
  ])

const pools = (search = '0', write = ['7', '5']) =>
  ok([
    { node_name: 'node-alpha-01', name: 'search', queue: search, rejected: '0' },
    { node_name: 'node-alpha-01', name: 'write', queue: write[0], rejected: '0' },
    { node_name: 'node-b', name: 'search', queue: '0', rejected: '0' },
    { node_name: 'node-b', name: 'write', queue: write[1], rejected: '0' },
  ])

const GREEN: Replies = { health: health('green'), nodes: nodes(), pools: pools() }

/** The world beneath the plugin: clock, store, osget, the file, the engine's own band. */
function world(on: On, replies: Replies, store: Record<string, unknown> = {}, help: Reply = { exitCode: 0, stdout: 'usage: osget <path>' }) {
  const clock = mock.clock(on, { now: START })
  const stored = new Map<string, unknown>(Object.entries(store))
  const calls: string[][] = []
  const registered: string[] = []
  const timeouts: (number | undefined)[] = []
  const toasts: string[] = []
  const state: { replies: Replies; rejectWith: string | null } = { replies, rejectWith: null }

  // Ops beneath the plugin answer { value }, as the engine's bottom does.
  on('store.get', ($, e) => ({ value: stored.get(e.key) }))
  on('store.set', ($, e) => {
    stored.set(e.key, e.value)
    return { value: undefined }
  })
  on('process.run', ($, e) => {
    calls.push([...e.argv])
    timeouts.push(e.init?.timeoutMs)
    const path = e.argv[1] ?? ''
    // The engine's bottom answering { deny } makes the plugin's $.process.run reject.
    if (state.rejectWith !== null && path !== '--help') return { deny: state.rejectWith }
    const reply: Reply =
      path === '--help'
        ? help
        : path === '_cluster/health'
          ? state.replies.health
          : path.startsWith('_cat/nodes')
            ? state.replies.nodes
            : state.replies.pools
    return {
      value: {
        exitCode: reply.exitCode,
        stdout: reply.stdout ?? '',
        stderr: reply.stderr ?? '',
        isStdoutTruncated: false,
        isStderrTruncated: false,
      },
    }
  })
  on('fs.stat', () => ({ value: { kind: 'file', size: 4096, mtimeMs: 0, isLink: false } }))
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('command.register', ($, e) => {
    registered.push(e.name)
    return { value: { command: e.name } }
  })
  on('ui.toast', ($, e) => {
    toasts.push(e.text)
    return { value: undefined }
  })
  // The engine's own band: what shows when the plugin passes (hidden).
  on('ui.render', { component: 'AbovePrompt' }, ($, e) => $.ui.resolve(e).Text({ children: ENGINE }))

  return { clock, calls, registered, timeouts, toasts, state, stored }
}

/** The whole line as shown: every string in the drawn tree, in order. */
function lineOf(node: unknown): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(lineOf).join('')
  if (node !== null && typeof node === 'object') {
    const props = (node as { props?: { children?: unknown } }).props
    const children = (node as { children?: unknown }).children ?? props?.children
    return lineOf(children)
  }
  return ''
}

// Text keys do not survive into the drawn tree (only a Box keeps one), so a
// segment is found by its own text.
const SEGMENT = {
  dot: /^[●○]$/,
  status: /^(green|yellow|red|unreachable)$/,
  cpu: /^CPU max/,
  heap: /^heap max/,
  searchq: /^search q /,
  writeq: /^write q /,
  unassigned: /^unassigned /,
} as const

type Drawing = { findAll: (query: { type: string }) => Promise<{ text: string; props: Record<string, unknown> }[]> }

async function seg(ui: Drawing, name: keyof typeof SEGMENT) {
  return (await ui.findAll({ type: 'Text' })).find(one => SEGMENT[name].test(one.text))
}

const toggle = { command: 'osmon-band', args: '', origin: { kind: 'composer' }, presentation: { isFullscreen: false, columns: 120 } } as const

describe('opensearch-monitor-band', () => {
  test('green renders the line with status green', { options: { osget_path: OSGET } }, async ($, on) => {
    const w = world(on, GREEN)
    await $.session.start(SESSION)
    await w.clock.settle()

    for (const surface of SURFACES) {
      const ui = await $.ui.mount({ plugin: PLUGIN, surface, ...BAND })
      expect(lineOf(await ui.drawn())).toBe(
        '● OpenSearch green · CPU max 62% (node-alp) · heap max 71% · search q 0 · write q 12 · unassigned 0 · 12:04 UTC',
      )
      expect((await seg(ui, 'dot'))?.props.color).toBe('success')
      expect((await seg(ui, 'status'))?.props.color).toBe('success')
      for (const key of ['cpu', 'heap', 'searchq', 'writeq', 'unassigned'] as const) {
        expect((await seg(ui, key))?.props.color).toBeUndefined()
      }
      await ui.unmount()
    }
    expect(w.toasts).toEqual([])
  })

  test('yellow colors the status yellow and flags unassigned shards', { options: { osget_path: OSGET } }, async ($, on) => {
    const w = world(on, { ...GREEN, health: health('yellow', 3) })
    await $.session.start(SESSION)
    await w.clock.settle()

    for (const surface of SURFACES) {
      const ui = await $.ui.mount({ plugin: PLUGIN, surface, ...BAND })
      expect(lineOf(await ui.drawn())).toContain('● OpenSearch yellow · ')
      expect((await seg(ui, 'status'))?.props.color).toBe('warning')
      expect((await seg(ui, 'unassigned'))?.text).toBe('unassigned 3')
      expect((await seg(ui, 'unassigned'))?.props.color).toBe('warning')
      await ui.unmount()
    }
  })

  test('red colors the dot and status red', { options: { osget_path: OSGET } }, async ($, on) => {
    const w = world(on, { ...GREEN, health: health('red', 12) })
    await $.session.start(SESSION)
    await w.clock.settle()

    for (const surface of SURFACES) {
      const ui = await $.ui.mount({ plugin: PLUGIN, surface, ...BAND })
      expect(lineOf(await ui.drawn())).toContain('● OpenSearch red · ')
      expect((await seg(ui, 'dot'))?.props.color).toBe('error')
      expect((await seg(ui, 'status'))?.props.color).toBe('error')
      await ui.unmount()
    }
  })

  test('CPU at or above 85% and busy queues get the warning color', { options: { osget_path: OSGET } }, async ($, on) => {
    const w = world(on, { health: health('green'), nodes: nodes('85', '91'), pools: pools('1', ['101', '0']) })
    await $.session.start(SESSION)
    await w.clock.settle()

    for (const surface of SURFACES) {
      const ui = await $.ui.mount({ plugin: PLUGIN, surface, ...BAND })
      expect((await seg(ui, 'cpu'))?.text).toBe('CPU max 85% (node-alp)')
      expect((await seg(ui, 'cpu'))?.props.color).toBe('warning')
      expect((await seg(ui, 'heap'))?.props.color).toBe('warning')
      expect((await seg(ui, 'searchq'))?.props.color).toBe('warning')
      expect((await seg(ui, 'writeq'))?.text).toBe('write q 101')
      expect((await seg(ui, 'writeq'))?.props.color).toBe('warning')
      expect((await seg(ui, 'unassigned'))?.props.color).toBeUndefined()
      await ui.unmount()
    }
  })

  test('84% CPU and a write queue of 100 stay uncolored', { options: { osget_path: OSGET } }, async ($, on) => {
    const w = world(on, { health: health('green'), nodes: nodes('84', '84'), pools: pools('0', ['100', '0']) })
    await $.session.start(SESSION)
    await w.clock.settle()

    for (const surface of SURFACES) {
      const ui = await $.ui.mount({ plugin: PLUGIN, surface, ...BAND })
      for (const key of ['cpu', 'heap', 'searchq', 'writeq'] as const) {
        expect((await seg(ui, key))?.props.color).toBeUndefined()
      }
      await ui.unmount()
    }
  })

  test('unreachable (exit 5) shows the last ok time', { options: { osget_path: OSGET } }, async ($, on) => {
    const w = world(on, GREEN)
    await $.session.start(SESSION)
    await w.clock.settle()

    w.state.replies = { ...GREEN, health: { exitCode: 5, stderr: 'osget: curl failed (exit 28)' } }
    await w.clock.advance(60_000)

    for (const surface of SURFACES) {
      const ui = await $.ui.mount({ plugin: PLUGIN, surface, ...BAND })
      expect(lineOf(await ui.drawn())).toBe('○ OpenSearch unreachable · last ok 12:04 UTC · unreachable')
      expect((await seg(ui, 'dot'))?.props.color).toBe('inactive')
      expect((await seg(ui, 'status'))?.props.color).toBe('inactive')
      expect(await seg(ui, 'cpu')).toBeUndefined()
      await ui.unmount()
    }
  })

  test('a $.process.run timeout (rejection) shows unreachable with the last ok time', { options: { osget_path: OSGET } }, async ($, on) => {
    const w = world(on, GREEN)
    await $.session.start(SESSION)
    await w.clock.settle()

    // The child outlived timeoutMs: the engine kills it and the call rejects.
    w.state.rejectWith = 'process.run: timed out after 20000 ms'
    await w.clock.advance(60_000)

    for (const surface of SURFACES) {
      const ui = await $.ui.mount({ plugin: PLUGIN, surface, ...BAND })
      expect(lineOf(await ui.drawn())).toBe('○ OpenSearch unreachable · last ok 12:04 UTC · unreachable')
      expect((await seg(ui, 'dot'))?.props.color).toBe('inactive')
      await ui.unmount()
    }

    // Back again: the next good tick restores the full line.
    w.state.rejectWith = null
    await w.clock.advance(60_000)
    for (const surface of SURFACES) {
      const ui = await $.ui.mount({ plugin: PLUGIN, surface, ...BAND })
      expect(lineOf(await ui.drawn())).toStartWith('● OpenSearch green · ')
      expect(lineOf(await ui.drawn())).toEndWith(' · 12:06 UTC')
      await ui.unmount()
    }
  })

  test('a second session.start leaves one command and one interval', { options: { osget_path: OSGET } }, async ($, on) => {
    const w = world(on, GREEN)
    await $.session.start(SESSION)
    await w.clock.settle()
    await $.session.start(SESSION)
    await w.clock.settle()
    const polls = () => w.calls.filter(argv => argv[1] === '_cluster/health').length

    expect(new Set(w.registered)).toEqual(new Set(['osmon-band']))
    const before = polls()
    await w.clock.advance(60_000)
    expect(polls()).toBe(before + 1)
    await w.clock.advance(60_000)
    expect(polls()).toBe(before + 2)
  })

  test('failure reasons: exit 3, exit 4, other exits use stderr cut to 60', { options: { osget_path: OSGET } }, async ($, on) => {
    const w = world(on, { ...GREEN, nodes: { exitCode: 3, stderr: 'osget: no password' } })
    await $.session.start(SESSION)
    await w.clock.settle()

    for (const surface of SURFACES) {
      const ui = await $.ui.mount({ plugin: PLUGIN, surface, ...BAND })
      // never ok yet: no "last ok" segment
      expect(lineOf(await ui.drawn())).toBe('○ OpenSearch unreachable · credentials or config')
      await ui.unmount()
    }

    w.state.replies = { ...GREEN, pools: { exitCode: 4, stdout: '{"error":"x"}' } }
    await w.clock.advance(60_000)
    for (const surface of SURFACES) {
      const ui = await $.ui.mount({ plugin: PLUGIN, surface, ...BAND })
      expect(lineOf(await ui.drawn())).toBe('○ OpenSearch unreachable · HTTP error')
      await ui.unmount()
    }

    const long = `osget: ${'x'.repeat(80)}`
    w.state.replies = { ...GREEN, health: { exitCode: 2, stderr: `\n${long}\nsecond line` } }
    await w.clock.advance(60_000)
    for (const surface of SURFACES) {
      const ui = await $.ui.mount({ plugin: PLUGIN, surface, ...BAND })
      expect(lineOf(await ui.drawn())).toBe(`○ OpenSearch unreachable · ${long.slice(0, 60)}`)
      await ui.unmount()
    }
  })

  test(
    'show_when_green=false hides the band while green and quiet',
    { options: { osget_path: OSGET, show_when_green: false } },
    async ($, on) => {
      const w = world(on, GREEN)
      await $.session.start(SESSION)
      await w.clock.settle()

      for (const surface of SURFACES) {
        const ui = await $.ui.mount({ plugin: PLUGIN, surface, ...BAND })
        expect(lineOf(await ui.drawn())).toBe(ENGINE)
        await ui.unmount()
      }

      w.state.replies = { ...GREEN, pools: pools('0', ['150', '0']) }
      await w.clock.advance(60_000)
      for (const surface of SURFACES) {
        const ui = await $.ui.mount({ plugin: PLUGIN, surface, ...BAND })
        expect((await seg(ui, 'status'))?.text).toBe('green')
        expect((await seg(ui, 'writeq'))?.props.color).toBe('warning')
        await ui.unmount()
      }
    },
  )

  test('missing osget_path hides the band and toasts once', { options: { osget_path: '' } }, async ($, on) => {
    const w = world(on, GREEN)
    await $.session.start(SESSION)
    await w.clock.settle()
    await w.clock.advance(120_000)

    for (const surface of SURFACES) {
      const ui = await $.ui.mount({ plugin: PLUGIN, surface, ...BAND })
      expect(lineOf(await ui.drawn())).toBe(ENGINE)
      await ui.unmount()
    }
    expect(w.toasts).toEqual([NOT_CONFIGURED])
    expect(w.calls).toEqual([])
  })

  test('osget that fails --help counts as not configured', { options: { osget_path: OSGET } }, async ($, on) => {
    const w = world(on, GREEN, {}, { exitCode: 126, stderr: 'permission denied' })
    await $.session.start(SESSION)
    await w.clock.settle()

    for (const surface of SURFACES) {
      const ui = await $.ui.mount({ plugin: PLUGIN, surface, ...BAND })
      expect(lineOf(await ui.drawn())).toBe(ENGINE)
      await ui.unmount()
    }
    expect(w.toasts).toEqual([NOT_CONFIGURED])
    expect(w.calls.filter(argv => argv[1] !== '--help')).toEqual([])
  })

  test('/osmon-band toggles the band and persists it', { options: { osget_path: OSGET } }, async ($, on) => {
    const w = world(on, GREEN)
    await $.session.start(SESSION)
    await w.clock.settle()

    const off = await $.command.run(toggle)
    expect(off.text).toBe('OpenSearch band off.')
    expect(w.stored.get('enabled')).toBe(false)
    for (const surface of SURFACES) {
      const ui = await $.ui.mount({ plugin: PLUGIN, surface, ...BAND })
      expect(lineOf(await ui.drawn())).toBe(ENGINE)
      await ui.unmount()
    }

    // off: no polling
    const before = w.calls.length
    await w.clock.advance(120_000)
    expect(w.calls.length).toBe(before)

    const back = await $.command.run(toggle)
    expect(back.text).toBe('OpenSearch band on.')
    expect(w.stored.get('enabled')).toBe(true)
    await w.clock.settle()
    for (const surface of SURFACES) {
      const ui = await $.ui.mount({ plugin: PLUGIN, surface, ...BAND })
      expect((await seg(ui, 'status'))?.text).toBe('green')
      await ui.unmount()
    }
  })

  test('a stored "off" keeps the band hidden at start', { options: { osget_path: OSGET } }, async ($, on) => {
    const w = world(on, GREEN, { enabled: false })
    await $.session.start(SESSION)
    await w.clock.settle()

    for (const surface of SURFACES) {
      const ui = await $.ui.mount({ plugin: PLUGIN, surface, ...BAND })
      expect(lineOf(await ui.drawn())).toBe(ENGINE)
      await ui.unmount()
    }
    expect(w.calls.filter(argv => argv[1] !== '--help')).toEqual([])
  })

  test('each tick runs exactly the three osget calls with a 20 s timeout', { options: { osget_path: OSGET } }, async ($, on) => {
    const w = world(on, GREEN)
    await $.session.start(SESSION)
    await w.clock.settle()

    const data = w.calls.map((argv, i) => ({ argv, timeoutMs: w.timeouts[i] })).filter(one => one.argv[1] !== '--help')
    expect(data).toEqual([
      { argv: [OSGET, '_cluster/health'], timeoutMs: 20000 },
      { argv: [OSGET, '_cat/nodes?format=json&h=name,node.role,cpu,heap.percent'], timeoutMs: 20000 },
      { argv: [OSGET, '_cat/thread_pool/search,write?format=json&h=node_name,name,queue,rejected'], timeoutMs: 20000 },
    ])
  })

  test('interval_seconds is clamped to 15', { options: { osget_path: OSGET, interval_seconds: 5 } }, async ($, on) => {
    const w = world(on, GREEN)
    await $.session.start(SESSION)
    await w.clock.settle()
    const polls = () => w.calls.filter(argv => argv[1] === '_cluster/health').length

    expect(polls()).toBe(1)
    await w.clock.advance(5_000)
    expect(polls()).toBe(1)
    await w.clock.advance(10_000)
    expect(polls()).toBe(2)
    for (const argv of w.calls) expect(argv[0]).toBe(OSGET)
  })
})
