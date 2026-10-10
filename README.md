# opensearch-monitor-kit

A read-only OpenSearch monitoring agent for Claude Code. Ask Claude why the cluster is slow, red or rejecting requests, and it checks cluster health, finds the root cause of CPU and queue spikes, audits Security Analytics, alerting and ISM, and optionally lines that up with Vector pipeline metrics from Grafana. It reports findings and writes remediation steps for a human to apply. It never changes the cluster.

This repo is a Claude Code plugin marketplace with two plugins:

| Plugin | What it adds | Status |
|---|---|---|
| `opensearch-monitor` | The `opensearch-health` skill (runbook), the `opensearch-monitor` agent, the GET-only `osget` client, a guard hook that blocks write requests to the cluster, and an optional Grafana MCP server | Stable APIs |
| `opensearch-monitor-band` | A one-line live cluster health band above the prompt, toggled with `/osmon-band` | Early access |

## Install

In Claude Code:

```
/plugin marketplace add your-github-user/opensearch-monitor-kit
/plugin install opensearch-monitor@opensearch-monitor-kit
/plugin install opensearch-monitor-band@opensearch-monitor-kit
```

The second install (the status band) is optional. Then restart Claude Code.

By hand, without the marketplace:

```sh
git clone https://github.com/your-github-user/opensearch-monitor-kit.git
claude --plugin-dir opensearch-monitor-kit/plugins/opensearch-monitor
```

To load it every session, add the plugin directory to `CLAUDE_CODE_PLUGIN_DIRS` in the `env` block of `~/.claude/settings.json`:

```json
{
  "env": {
    "CLAUDE_CODE_PLUGIN_DIRS": "/path/to/opensearch-monitor-kit/plugins/opensearch-monitor"
  }
}
```

## Configure

### Cluster URL and user

Add these to the `env` block in `~/.claude/settings.json`:

```json
{
  "env": {
    "OSMON_URL": "https://opensearch.example.com",
    "OSMON_USER": "osmon-reader"
  }
}
```

`OSMON_URL` is the base URL with no trailing path and no `user:pass@` part. `OSMON_USER` is the basic-auth username.

### Password

`osget` looks for the password in this order: `OSMON_PASSWORD_FILE`, then the macOS Keychain, then `OSMON_PASSWORD`. The password is passed to curl on stdin, never on its command line.

**macOS (Keychain).** Run:

```sh
security add-generic-password -a osmon-reader -s osmon -w
```

Use your `OSMON_USER` value after `-a`. Because `-w` is the last option and has no value, `security` prompts you to type the password (twice) instead of taking it from the command line, so it never lands in your shell history or the process list. The service name defaults to `osmon`; set `OSMON_KEYCHAIN_SERVICE` to use a different one.

**Linux or CI (password file).** Put only the password in a file that only you can read, and point `OSMON_PASSWORD_FILE` at it:

```sh
mkdir -p ~/.config/osmon && chmod 700 ~/.config/osmon
( umask 077; IFS= read -rs pw && printf '%s' "$pw" > ~/.config/osmon/password )
chmod 600 ~/.config/osmon/password
```

`read -s` waits for you to type the password without echoing it, so it stays out of your shell history. Writing the file with an editor and then running `chmod 600` works too; a trailing newline is ignored.

```json
{ "env": { "OSMON_PASSWORD_FILE": "/home/you/.config/osmon/password" } }
```

`osget` refuses a password file that other users can read (mode must be 600 or 400).

**Environment variable (least preferred).** `OSMON_PASSWORD` works, but the value then sits in `settings.json` in plain text and is visible to every process Claude Code starts. Use it only when the other two are not possible.

### Other settings

| Variable | Default | Meaning |
|---|---|---|
| `OSMON_INSECURE` | unset | `1` skips TLS certificate verification (self-signed certs). Off by default. |
| `OSMON_TIMEOUT` | `60` | Request timeout in seconds. |
| `OSMON_GUARD_HOSTS` | unset | Comma-separated extra hostnames the guard hook protects, for example other nodes or a load balancer name that also reach the cluster. |
| `OSMON_KEYCHAIN_SERVICE` | `osmon` | Keychain service name (macOS). |

### Check the setup

Run `osget --check`. It prints which password source it used (never the password) and the cluster name and version.

For a marketplace install the script lives under `~/.claude/plugins/cache/...`. Find it with:

```sh
find ~/.claude/plugins -name osget -path '*opensearch-health*'
```

Then run it:

```sh
/path/printed/above/osget --check
```

For a clone, it is `plugins/opensearch-monitor/skills/opensearch-health/scripts/osget`. Run `osget --help` for the full usage.

### Use a read-only OpenSearch user

The kit only sends GET requests, but the real protection is a cluster user that cannot write. Create a dedicated user mapped to a read-only role. This is a starting point for the OpenSearch Security plugin; adjust it to your version and to what you want the agent to see:

```json
PUT _plugins/_security/api/roles/osmon_read
{
  "cluster_permissions": [
    "cluster_monitor",
    "cluster:admin/opendistro/alerting/monitor/get",
    "cluster:admin/opendistro/alerting/monitor/search",
    "cluster:admin/opendistro/ism/managedindex/explain",
    "cluster:admin/opensearch/securityanalytics/detector/get",
    "cluster:admin/opensearch/securityanalytics/detector/search",
    "cluster:admin/opensearch/securityanalytics/findings/get",
    "cluster:admin/opensearch/securityanalytics/alerts/get"
  ],
  "index_permissions": [
    {
      "index_patterns": ["*"],
      "allowed_actions": ["indices_monitor", "read"]
    }
  ]
}
```

Map your user to it (`PUT _plugins/_security/api/rolesmapping/osmon_read`), and narrow `index_patterns` if the agent should not read document contents everywhere. Run these as an admin yourself; the kit never sends them.

## Use it

Ask in plain language. The `opensearch-health` skill loads on OpenSearch questions:

- "Why is my OpenSearch CPU high?"
- "The cluster went red, what happened?"
- "Audit my cluster"
- "Ask the opensearch-monitor agent to check health"

Reports end with a status line, an evidence table, the likely root cause with a confidence level, and remediation steps marked CHANGE for you to apply.

## Read-only guarantee and its limits

- **`osget` can only send GET.** No flag or environment variable changes the method. A request body (for `_search` endpoints) is still sent with GET.
- **The guard hook** checks every Bash command Claude runs. If the command names the cluster host (the host in `OSMON_URL`, any host in `OSMON_GUARD_HOSTS`, or a `$OSMON_URL` reference), it blocks non-GET requests (`curl -X POST`, `-d`, `--data*`, `-F`, `-T`, `--json`, `wget --post-*`, `http PUT ...`, Python or Node one-liners that post) and any credentials passed on the command line (`-u`, `--user`, `Authorization:` headers, `user:pass@host`). Everything else, including every `osget` call, is allowed.

The hook does not catch:

- script files (`python3 x.py`), since it reads only the command line;
- other clients, such as `nc`, `openssl s_client`, `perl` or the `aws` CLI;
- commands run on cluster nodes over `ssh`;
- header values read from files (`curl -H @headers.txt`);
- anything outside Claude Code.

The real control is a read-only role on the cluster.

## Grafana / Vector (optional)

The runbook can line up OpenSearch events with Vector pipeline metrics (buffers, 503s, discarded events) from a Prometheus datasource in Grafana.

1. Install [`mcp-grafana`](https://github.com/grafana/mcp-grafana) so the `mcp-grafana` binary is on your `PATH`.
2. Set `GRAFANA_URL` and `GRAFANA_SERVICE_ACCOUNT_TOKEN` in the `env` block of `~/.claude/settings.json`. A Viewer service account is enough.

Without the binary, Claude Code lists the plugin's `grafana` MCP server as failed to connect. That is harmless: the rest of the plugin works and the report says the Vector step was skipped. If you already run a Grafana MCP server, the agent uses it instead.

## Status band (early access)

The `opensearch-monitor-band` plugin shows one line above the prompt, for example:

```
● OpenSearch green · CPU max 62% (node-a) · heap max 71% · search q 0 · write q 12 · unassigned 0 · 12:04 UTC
```

- Set `osget_path` to the absolute path of `osget` (see "Check the setup") in `/config`. Until you do, the band stays hidden and shows a one-time reminder. You can also set the refresh interval (`interval_seconds`, default 60, minimum 15) and whether to show the band while the cluster is green (`show_when_green`).
- `/osmon-band` turns the band on or off.
- It needs a session running the Claude Code CLI engine: the terminal, or the desktop app's Code tab.
- It is built on an early-access API that may change between Claude Code releases.

The band calls `osget` like any other use, so it is GET-only and never handles credentials itself.

## Requirements

- macOS or Linux
- `bash` (3.2 or newer), `curl`, `python3`
- Optional: macOS `security` (Keychain), `mcp-grafana`

## Development

```sh
claude plugin validate .
claude plugin validate plugins/opensearch-monitor
claude plugin validate plugins/opensearch-monitor-band
bash plugins/opensearch-monitor/tests/run.sh
claude plugin test plugins/opensearch-monitor-band
bash tests/scrub.sh
```

`tests/scrub.sh` checks the repo for IP addresses, emails, 12-digit account-like numbers, private keys and passwords before a push. Pass `--extra <file>` with your own terms (hostnames, tenant names), one per line; keep that file outside the repo.

## License

MIT. See [LICENSE](LICENSE).
