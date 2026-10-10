# opensearch-monitor-band

A one-line OpenSearch health band above the Claude Code prompt: cluster status, max CPU and heap, search and write queues, unassigned shards. It is built on an early-access API that may change between Claude Code releases.

What it adds:

- **Band**: a line above the prompt, refreshed by calling `osget` from the `opensearch-monitor` plugin. GET-only; it never handles credentials itself.
- **Command `/osmon-band`**: turns the band on or off.
- **Settings** (in `/config`): `osget_path` (required, absolute path to `osget`), `interval_seconds` (default 60, minimum 15), `show_when_green` (default true).

It needs the `opensearch-monitor` plugin configured, and a session running the Claude Code CLI engine (the terminal, or the desktop app's Code tab).

Install and configuration are in the [root README](../../README.md#status-band-early-access).

Tests: `claude plugin test plugins/opensearch-monitor-band` from the repo root.
