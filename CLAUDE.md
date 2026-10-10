# opensearch-monitor-kit

Claude Code plugin marketplace: two plugins under `plugins/`. Spec: `docs/SPEC.md`. Tasks: `docs/PLAN.md`. Index: `filemap.md`.

Stack: bash 3.2+ (osget), python3 stdlib (guard, tests), TypeScript function-hooks mod (band, early-access API).

Commands:
- Validate: `claude plugin validate .` then `claude plugin validate plugins/opensearch-monitor` and `plugins/opensearch-monitor-band`
- Tests: `bash plugins/opensearch-monitor/tests/run.sh`; `claude plugin test plugins/opensearch-monitor-band`
- Scrub: `bash tests/scrub.sh`

Rules:
- Public repo: never commit real hostnames, tenants, index names, IDs, IPs, emails, usernames. Examples use `opensearch.example.com`, tenants `acme`/`globex`.
- Cluster access is GET-only through `osget`; never add a code path that sends another method.
- Secrets only via stdin (`curl -K -`), never argv, env of child processes, logs or files the kit writes.
- Shell must run on macOS bash 3.2 and BSD tools as well as Linux.
- Update `filemap.md` when adding or moving files.
