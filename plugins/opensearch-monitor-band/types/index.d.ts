// State contract of the opensearch-monitor-band mod: the values the band
// draws from, held by the host for the session (they survive a hot reload).
// Self-contained on purpose: no import, no reference.

/** Cluster status as `_cluster/health` reports it. */
export type BandStatus = 'green' | 'yellow' | 'red'

/** One successful poll, reduced to what the band line shows. */
export type BandSnapshot = {
  status: BandStatus
  /** Highest node CPU %, null when `_cat/nodes` listed none. */
  cpuMax: number | null
  /** Node holding `cpuMax`, cut to 8 characters. */
  cpuNode: string | null
  /** Highest node heap %, null when none was listed. */
  heapMax: number | null
  /** Sum of `queue` over every node's `search` pool. */
  searchQueue: number
  /** Sum of `queue` over every node's `write` pool. */
  writeQueue: number
  /** `unassigned_shards` from `_cluster/health`. */
  unassigned: number
  /** When this poll succeeded, UTC `HH:MM`. */
  at: string
}

declare module 'claude-code' {
  interface PluginState {
    'opensearch-monitor-band': {
      /** The last successful poll; null before the first one. */
      last: BandSnapshot | null
      /** Why the latest poll failed; null when it succeeded. */
      error: string | null
      /** osget_path is set and answers `--help` with exit 0. */
      isConfigured: boolean
      /** The /osmon-band toggle (persisted in $.store as `enabled`). */
      isEnabled: boolean
    }
  }
}
