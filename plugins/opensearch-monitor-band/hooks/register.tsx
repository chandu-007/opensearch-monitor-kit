// opensearch-monitor-band: one line above the prompt with the cluster's
// health (SPEC "Status band mod", Appendix C). Polls only through osget,
// which holds the credentials; this module never sees or passes them.
import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, Timer } from 'claude-code'

import type { BandSnapshot, BandStatus } from '../types'

const last = atom({ plugin: 'opensearch-monitor-band', key: 'last' } as const, null)
const error = atom({ plugin: 'opensearch-monitor-band', key: 'error' } as const, null)
const isConfigured = atom({ plugin: 'opensearch-monitor-band', key: 'isConfigured' } as const, false)
const isEnabled = atom({ plugin: 'opensearch-monitor-band', key: 'isEnabled' } as const, true)

const COMMAND = 'osmon-band'
const STORE_ENABLED = 'enabled'
const NOT_CONFIGURED = 'opensearch-monitor-band: set osget_path in /config'
const CALL_TIMEOUT_MS = 20000
const CHECK_TIMEOUT_MS = 10000
const MIN_INTERVAL_SECONDS = 15

const HEALTH = '_cluster/health'
const NODES = '_cat/nodes?format=json&h=name,node.role,cpu,heap.percent'
const POOLS = '_cat/thread_pool/search,write?format=json&h=node_name,name,queue,rejected'

const PERCENT_WARN = 85
const SEARCH_QUEUE_WARN = 0
const WRITE_QUEUE_WARN = 100

// Theme keys, so the band follows the person's light or dark theme.
const STATUS_COLOR: Record<BandStatus, string> = { green: 'success', yellow: 'warning', red: 'error' }
const UNREACHABLE_COLOR = 'inactive'
const WARN_COLOR = 'warning'

type Run = { exitCode: number; stdout: string; stderr: string }
type Outcome = { ok: true; stdout: string } | { ok: false; reason: string }

const cut = (text: string, max: number) => (text.length > max ? text.slice(0, max) : text)

const firstLine = (text: string) => text.split('\n').find(line => line.trim() !== '')?.trim() ?? ''

/** The short reason the unreachable line shows for a failed osget run. */
const reasonFor = (run: Run): string => {
  if (run.exitCode === 3) return 'credentials or config'
  if (run.exitCode === 4) return 'HTTP error'
  if (run.exitCode === 5) return 'unreachable'
  const line = cut(firstLine(run.stderr), 60)
  return line === '' ? `osget exit ${run.exitCode}` : line
}

const reasonForRejection = (thrown: unknown): string => {
  const message = thrown instanceof Error ? thrown.message : String(thrown)
  if (/time(d)?\s*out|timeout/i.test(message)) return 'unreachable'
  const line = cut(firstLine(message), 60)
  return line === '' ? 'osget failed' : line
}

const num = (value: unknown): number => {
  if (typeof value === 'number') return value
  if (typeof value === 'string' && value.trim() !== '') return Number(value)
  return Number.NaN
}

const isStatus = (value: unknown): value is BandStatus =>
  value === 'green' || value === 'yellow' || value === 'red'

const utcClock = (ms: number) => new Date(ms).toISOString().slice(11, 16)

/** Reduces the three osget answers to a snapshot; throws on a shape it cannot read. */
const toSnapshot = (health: string, nodes: string, pools: string, at: string): BandSnapshot => {
  const h = JSON.parse(health) as Record<string, unknown>
  if (!isStatus(h.status)) throw new Error('unexpected health response')
  const unassigned = num(h.unassigned_shards)

  const nodeRows = JSON.parse(nodes) as unknown
  const poolRows = JSON.parse(pools) as unknown
  if (!Array.isArray(nodeRows) || !Array.isArray(poolRows)) throw new Error('unexpected _cat response')

  let cpuMax: number | null = null
  let cpuNode: string | null = null
  let heapMax: number | null = null
  for (const row of nodeRows as Record<string, unknown>[]) {
    const cpu = num(row.cpu)
    if (Number.isFinite(cpu) && (cpuMax === null || cpu > cpuMax)) {
      cpuMax = cpu
      cpuNode = cut(String(row.name ?? ''), 8)
    }
    const heap = num(row['heap.percent'])
    if (Number.isFinite(heap) && (heapMax === null || heap > heapMax)) heapMax = heap
  }

  let searchQueue = 0
  let writeQueue = 0
  for (const row of poolRows as Record<string, unknown>[]) {
    const queue = num(row.queue)
    if (!Number.isFinite(queue)) continue
    if (row.name === 'search') searchQueue += queue
    if (row.name === 'write') writeQueue += queue
  }

  return {
    status: h.status,
    cpuMax,
    cpuNode,
    heapMax,
    searchQueue,
    writeQueue,
    unassigned: Number.isFinite(unassigned) ? unassigned : 0,
    at,
  }
}

const warnings = (snap: BandSnapshot) => ({
  cpu: snap.cpuMax !== null && snap.cpuMax >= PERCENT_WARN,
  heap: snap.heapMax !== null && snap.heapMax >= PERCENT_WARN,
  search: snap.searchQueue > SEARCH_QUEUE_WARN,
  write: snap.writeQueue > WRITE_QUEUE_WARN,
  unassigned: snap.unassigned > 0,
})

const pathOption = (value: unknown) => (typeof value === 'string' ? value.trim() : '')

const intervalMs = (value: unknown) => {
  const seconds = typeof value === 'number' && Number.isFinite(value) ? value : 60
  return Math.max(MIN_INTERVAL_SECONDS, seconds) * 1000
}

const isAbsolute = (path: string) => path.startsWith('/') || /^[A-Za-z]:[\\/]/.test(path)

type Poller = { osget: string; isPolling: boolean }

/** One osget call; a non-zero exit or a rejection becomes a short reason. */
async function runOsget($: EngineInterface, osget: string, path: string): Promise<Outcome> {
  try {
    const ran = await $.process.run([osget, path], { timeoutMs: CALL_TIMEOUT_MS })
    return ran.exitCode === 0 ? { ok: true, stdout: ran.stdout } : { ok: false, reason: reasonFor(ran) }
  } catch (thrown) {
    return { ok: false, reason: reasonForRejection(thrown) }
  }
}

/** One tick: the three calls, then `last` on success or `error` on failure. */
async function poll($: EngineInterface, poller: Poller): Promise<void> {
  if (poller.isPolling) return
  if (!(await read($, isEnabled))) return
  poller.isPolling = true
  try {
    const [health, nodes, pools] = await Promise.all([
      runOsget($, poller.osget, HEALTH),
      runOsget($, poller.osget, NODES),
      runOsget($, poller.osget, POOLS),
    ])
    if (!health.ok || !nodes.ok || !pools.ok) {
      const reason = !health.ok ? health.reason : !nodes.ok ? nodes.reason : !pools.ok ? pools.reason : ''
      await update($, error, () => reason)
      return
    }
    let snap: BandSnapshot
    try {
      snap = toSnapshot(health.stdout, nodes.stdout, pools.stdout, utcClock(await $.clock.now()))
    } catch {
      await update($, error, () => 'unexpected response')
      return
    }
    await update($, last, () => snap)
    await update($, error, () => null)
  } finally {
    poller.isPolling = false
  }
}

/** osget_path is absolute, a file, and answers `--help` with exit 0. */
async function checkConfigured($: EngineInterface, osget: string): Promise<boolean> {
  if (osget === '' || !isAbsolute(osget)) return false
  const stat = await $.fs.stat(osget).catch(() => undefined)
  if (stat?.kind !== 'file') return false
  const help = await $.process.run([osget, '--help'], { timeoutMs: CHECK_TIMEOUT_MS }).catch(() => undefined)
  return help?.exitCode === 0
}

export const register: Register = (on, options) => {
  const osget = pathOption(options.osget_path)
  const everyMs = intervalMs(options.interval_seconds)
  const showWhenGreen = options.show_when_green !== false
  const poller: Poller = { osget, isPolling: false }
  let pollNow: (() => void) | null = null
  // One interval per load: a reload drops the old environment's timers, and a
  // second session.start in the same load cancels the first before starting.
  let timer: Timer | null = null

  on('session.start', async ($, e, next) => {
    await $.command.register({ name: COMMAND, description: 'Turn the OpenSearch health band on or off' })
    const stored = await $.store.get(STORE_ENABLED)
    await update($, isEnabled, () => stored !== false)

    void (async () => {
      const ok = await checkConfigured($, osget)
      await update($, isConfigured, () => ok)
      if (!ok) {
        $.ui.toast(NOT_CONFIGURED)
        return
      }
      pollNow = () => void poll($, poller)
      pollNow()
      timer?.cancel()
      timer = $.clock.every(everyMs, () => pollNow?.())
    })()

    return next(e)
  })

  on('command.run', { command: COMMAND }, async $ => {
    const now = await update($, isEnabled, was => !(was ?? true))
    await $.store.set(STORE_ENABLED, now)
    if (now) pollNow?.()
    return { text: now ? 'OpenSearch band on.' : 'OpenSearch band off.' }
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey) return next(e)
    if (!(await read($, isConfigured)) || !(await read($, isEnabled))) return next(e)

    const snap = await read($, last)
    const reason = await read($, error)
    const { Text } = $.ui.resolve(e)

    if (reason !== null && reason !== undefined) {
      return (
        <Text wrap="truncate">
          <Text color={UNREACHABLE_COLOR}>○</Text>
          {' OpenSearch '}
          <Text color={UNREACHABLE_COLOR}>unreachable</Text>
          {snap ? ' · ' : ''}
          {snap ? <Text>last ok {snap.at} UTC</Text> : ''}
          {' · '}
          <Text>{reason}</Text>
        </Text>
      )
    }

    if (!snap) return next(e)

    const warn = warnings(snap)
    const isQuiet = snap.status === 'green' && !Object.values(warn).some(Boolean)
    if (!showWhenGreen && isQuiet) return next(e)

    const tone = (isWarn: boolean) => (isWarn ? { color: WARN_COLOR } : {})
    const cpu = snap.cpuMax === null ? 'CPU max n/a' : `CPU max ${snap.cpuMax}% (${snap.cpuNode ?? ''})`
    const heap = snap.heapMax === null ? 'heap max n/a' : `heap max ${snap.heapMax}%`

    return (
      <Text wrap="truncate">
        <Text color={STATUS_COLOR[snap.status]}>●</Text>
        {' OpenSearch '}
        <Text color={STATUS_COLOR[snap.status]}>{snap.status}</Text>
        {' · '}
        <Text {...tone(warn.cpu)}>{cpu}</Text>
        {' · '}
        <Text {...tone(warn.heap)}>{heap}</Text>
        {' · '}
        <Text {...tone(warn.search)}>search q {snap.searchQueue}</Text>
        {' · '}
        <Text {...tone(warn.write)}>write q {snap.writeQueue}</Text>
        {' · '}
        <Text {...tone(warn.unassigned)}>unassigned {snap.unassigned}</Text>
        {' · '}
        <Text>{snap.at} UTC</Text>
      </Text>
    )
  })
}
