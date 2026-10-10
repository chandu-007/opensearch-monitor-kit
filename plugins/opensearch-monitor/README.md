# opensearch-monitor

Read-only OpenSearch health triage, root cause analysis and audits for Claude Code. It reports what it finds and writes remediation steps for a human; it never changes the cluster.

What it adds:

- **Skill `opensearch-health`**: the runbook. Setup check, triage ladder, how to read the numbers, Vector correlation, report format. Deeper checklists live in `skills/opensearch-health/reference/`.
- **Agent `opensearch-monitor`**: a read-only monitoring subagent that loads the skill and returns a structured report.
- **Script `osget`** (`skills/opensearch-health/scripts/osget`): a GET-only cluster client. Credentials reach curl on stdin, never on its command line. Run `osget --help` for usage and `osget --check` to test your setup.
- **Hook**: a `PreToolUse` guard on Bash (`hooks/guard.py`) that blocks non-GET requests and inline credentials sent to the cluster host.
- **MCP server `grafana`** (optional): runs `mcp-grafana` for Vector metrics. Without the binary it shows as failed to connect and everything else still works.

Install, configuration (`OSMON_URL`, `OSMON_USER`, Keychain or password file), the read-only role, and the guard's limits are in the [root README](../../README.md).

Tests: `bash tests/run.sh`.
