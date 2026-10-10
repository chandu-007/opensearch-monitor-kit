# Cluster triage in depth

## When to use

Open this for CPU, heap or load spikes, queued or rejected requests, slow ingest, an unbalanced node, or a yellow or red cluster. It expands steps 1 to 7 of the triage ladder in `SKILL.md`.

The script is `<this skill's base directory>/scripts/osget`; build the absolute path from the skill's base directory and write it in full in every command. All calls are GET. Any write is a **CHANGE (human decision)** for the user to run.

## 1. Health and nodes

```
'<this skill's base directory>/scripts/osget' '_cluster/health'
'<this skill's base directory>/scripts/osget' '_cat/nodes?v&h=name,node.role,cpu,load_1m,load_5m,heap.percent,ram.percent,disk.used_percent,uptime&s=cpu:desc' --raw
```

Look for:
- `status`, `unassigned_shards`, `initializing_shards`, `relocating_shards`, `number_of_pending_tasks`.
- One node far above the others in `cpu` or `load_1m`: go to step 6 (hotspots).
- `heap.percent` above 85 on several nodes: check GC in step 5.
- `disk.used_percent` above 85 (low watermark): new shards stop allocating to that node. Above 90 (high watermark) shards move away. Above 95 (flood stage) indices go read-only.
- `uptime`: a node with a much shorter uptime restarted. If every data node has an uptime within a minute of the others, the cluster had a non-rolling restart. That alone explains a red status and a burst of recovery work.

## 2. Thread pools

```
'<this skill's base directory>/scripts/osget' '_cat/thread_pool/search,write,refresh,get,force_merge,snapshot?v&h=node_name,name,active,queue,rejected,completed&s=queue:desc' --raw
```

- `active` equal to the pool size plus a growing `queue` means the pool is saturated.
- `rejected` and `completed` are counters since node start. A large `rejected` value may be weeks old. Take a second sample after the other steps (or about 60 s later) and compare. Only the difference is "rejected now".
- A node restart resets its counters. If a value went down, the node restarted.

## 3. Hot threads

```
'<this skill's base directory>/scripts/osget' '_nodes/hot_threads?threads=3&interval=1s&ignore_idle_threads=true' --raw | grep -E 'cpu usage by thread|:::'
```

Lines with `:::` name the node; lines with `cpu usage by thread` name the thread and its share of one core. Thread names:

| Thread | Workload |
|---|---|
| `[search]` | queries, including monitor and detector searches |
| `[write]` | bulk indexing |
| `Lucene Merge Thread` | segment merges after heavy indexing |
| `[generic]` | recovery, translog replay |
| `[refresh]` | refreshes (too many shards or short refresh interval) |
| `DefaultDispatcher-worker` | alerting or Security Analytics coroutines |

If you need the stack, run the call again without the `grep`. `percolat` in the stack means Security Analytics rule matching.

## 4. Running searches

```
'<this skill's base directory>/scripts/osget' '_tasks?actions=*search*&detailed=true&filter_path=nodes.*.tasks.*.description,nodes.*.tasks.*.running_time_in_nanos'
```

`_cat/tasks` can return 504 behind a proxy; `_tasks` with `filter_path` is smaller and faster. Group tasks by the `indices[...]` part of the description and sort by `running_time_in_nanos` (divide by 1e9 for seconds).

Patterns:
- A `_seq_no` range query with `size:10000` and a long `fields` list: a Security Analytics doc-level monitor. See `security-analytics.md`.
- Large aggregations over wide time ranges: dashboards or query-level monitors.
- Many tasks on one index pattern: the user or monitor behind that pattern drives the load.

## 5. Ingest, merges and GC (two samples)

```
'<this skill's base directory>/scripts/osget' '_nodes/stats/indices/indexing,refresh,merge,search'
'<this skill's base directory>/scripts/osget' '_nodes/stats/jvm,breaker'
```

The metric name is `merge` (singular) in the path; the response key is `merges`.

Save two samples to files and compute rates. Use your scratchpad or `mktemp -d` for the files. Take the second sample after the other steps, or about 60 s after the first.

```
'<this skill's base directory>/scripts/osget' '_nodes/stats/indices/indexing,refresh,merge,search' > /path/to/tmp/ns1.json
# ... later ...
'<this skill's base directory>/scripts/osget' '_nodes/stats/indices/indexing,refresh,merge,search' > /path/to/tmp/ns2.json
```

```
python3 - /path/to/tmp/ns1.json /path/to/tmp/ns2.json <<'EOF'
import json, sys
a = json.load(open(sys.argv[1]))["nodes"]
b = json.load(open(sys.argv[2]))["nodes"]
print("%-24s %10s %10s %8s %10s" % ("node", "docs/s", "refresh/s", "merges", "search/s"))
for nid, n2 in sorted(b.items(), key=lambda kv: kv[1]["name"]):
    n1 = a.get(nid)
    if not n1:
        continue  # node restarted or joined between samples
    dt = (n2["timestamp"] - n1["timestamp"]) / 1000.0
    if dt <= 0:
        continue
    i1, i2 = n1["indices"], n2["indices"]
    docs = (i2["indexing"]["index_total"] - i1["indexing"]["index_total"]) / dt
    refr = (i2["refresh"]["total"] - i1["refresh"]["total"]) / dt
    srch = (i2["search"]["query_total"] - i1["search"]["query_total"]) / dt
    print("%-24s %10.0f %10.1f %8d %10.1f" % (n2["name"][:24], docs, refr, i2["merges"]["current"], srch))
EOF
```

The time delta comes from each node's own `timestamp`, so the exact wait does not matter.

From `_nodes/stats/jvm,breaker` look at:
- `jvm.mem.heap_used_percent` above 85 for long periods.
- `jvm.gc.collectors.old.collection_count` and `collection_time_in_millis` growing between samples: old GC pressure.
- `breakers.*.tripped` greater than 0: requests were refused to protect the heap. `fielddata` and `request` breakers point to aggregations.

## 6. Hotspots

```
'<this skill's base directory>/scripts/osget' '_cat/shards?h=index,shard,prirep,node,indexing.index_total&format=json'
'<this skill's base directory>/scripts/osget' '_cat/allocation?v' --raw
'<this skill's base directory>/scripts/osget' '_cat/recovery?v&active_only=true' --raw
'<this skill's base directory>/scripts/osget' '_cat/pending_tasks?v' --raw
```

`_cat/shards` has no timestamp, so record the time yourself with each sample:

```
date -u +%s > /path/to/tmp/sh1.t; '<this skill's base directory>/scripts/osget' '_cat/shards?h=index,shard,prirep,node,indexing.index_total&format=json' > /path/to/tmp/sh1.json
```

Repeat as `sh2`, then:

```
python3 - /path/to/tmp/sh1 /path/to/tmp/sh2 <<'EOF'
import json, sys
def load(p):
    t = int(open(p + ".t").read().strip())
    rows = json.load(open(p + ".json"))
    return t, {(r["index"], r["shard"], r["prirep"]): r for r in rows}
t1, a = load(sys.argv[1]); t2, b = load(sys.argv[2])
dt = max(t2 - t1, 1)
out, per_node = [], {}
for k, r in b.items():
    if k not in a or r.get("indexing.index_total") is None or a[k].get("indexing.index_total") is None:
        continue
    rate = (int(r["indexing.index_total"]) - int(a[k]["indexing.index_total"])) / dt
    out.append((rate, k, r.get("node")))
    per_node[r.get("node")] = per_node.get(r.get("node"), 0) + rate
for rate, k, node in sorted(out, reverse=True)[:15]:
    print("%10.0f docs/s  %s[%s]%s  on %s" % (rate, k[0], k[1], k[2], node))
print("--- per node")
for node, rate in sorted(per_node.items(), key=lambda kv: -kv[1]):
    print("%10.0f docs/s  %s" % (rate, node))
EOF
```

Look for:
- One node carrying several of the busiest primaries: a placement hotspot. A human can spread them with `index.routing.allocation.total_shards_per_node` or more primaries at the next rollover (CHANGE).
- `_cat/allocation`: uneven `shards` or `disk.percent` across data nodes.
- `_cat/recovery`: active recoveries compete for disk and CPU.
- `_cat/pending_tasks`: a long queue of cluster-state updates (mappings, shard starts) slows everything; large numbers of dynamic mapping updates point to mapping explosion.

## 7. Yellow or red: unassigned shards, allocation and recovery

```
'<this skill's base directory>/scripts/osget' '_cat/shards?v&h=index,shard,prirep,state,unassigned.reason,node' --raw | grep -v STARTED
'<this skill's base directory>/scripts/osget' '_cluster/allocation/explain'
'<this skill's base directory>/scripts/osget' 'firewall-acme-2026.10.07/_recovery?active_only=true'
```

- Red: at least one primary is unassigned. Yellow: only replicas are unassigned.
- `unassigned.reason`: `NODE_LEFT` (node restart or crash), `ALLOCATION_FAILED` (repeated failures, see explain), `INDEX_CREATED` (new index not yet placed), `CLUSTER_RECOVERED` (full restart).
- `_cluster/allocation/explain` with no body explains the first unassigned shard. Read `allocate_explanation` and each node's `deciders`: disk watermark, `same_shard`, awareness, `max_retry`.
- If allocation stopped after too many failures (`max_retry`), the fix is `CHANGE (human decision): POST _cluster/reroute?retry_failed=true` once the cause is gone.

Recovery:
- In `_recovery`, check `stage`, `translog.recovered`, `translog.total` and `index.size.percent`.
- A replica stuck in the `translog` stage with `recovered` still rising is replaying operations one by one. This is very slow for percolator (rule) indices. Options for a human: wait; or drop and rebuild the replica with `CHANGE (human decision): PUT <index>/_settings {"index.number_of_replicas":0}` followed later by `{"index.number_of_replicas":1}`.
- A recovery in `index` stage with low throughput may be throttled by `indices.recovery.max_bytes_per_sec`. Check with `'<this skill's base directory>/scripts/osget' '_cluster/settings?include_defaults=true&filter_path=*.indices.recovery*'`.

## Writing it up

Put each sample's time (UTC) in the timeline. Report rates, not raw counters. State which numbers came from a single sample.
