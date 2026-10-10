# Plan

Status: `[ ]` open, `[x]` accepted. Each task references docs/SPEC.md; workers update filemap.md when they add files.

1. [x] **Scaffold** (light). Create every directory and manifest in SPEC "Repository layout": `.claude-plugin/marketplace.json` (name `opensearch-monitor-kit`, owner placeholder `your-github-user`, two plugins with `source` paths, version 0.1.0), both `plugin.json` files (band's `userConfig` per SPEC "Status band mod", `types` field), `hooks/hooks.json` for both plugins (classic: PreToolUse/Bash → `python3 "${CLAUDE_PLUGIN_ROOT}/hooks/guard.py"`; band: `{ "modules": ["./register.tsx"] }`), MIT LICENSE (copyright holder placeholder), empty-but-valid stubs for SKILL.md, agent, guard.py, osget, register.tsx, types/index.d.ts. Verify: `claude plugin validate .` and on each plugin folder (report output verbatim; if the CLI cannot run in the worker's shell, say so).

2. [x] **osget client + tests** (careful). Implement SPEC "osget" exactly, bash 3.2-compatible. `tests/run.sh` with a stub `curl` on PATH that records argv, stdin and method to temp files; cases: method always GET, password never in argv/env, path validation rejects absolute URL/`..`/spaces, body sent as file with GET, password file perms enforced, exit codes, `--check` never prints password, password with quotes/backslashes escaped. Depends on 1.

3. [x] **Guard hook + tests** (careful). Implement SPEC "Guard hook" in `hooks/guard.py` (python3 stdlib only). Tests in `tests/guard_test.py` (run from `tests/run.sh`) feeding hook JSON: allow osget, allow unrelated curl, allow plain GET curl to host without credentials, deny curl to host with `-u`/`--user`/`Authorization:` (creds on argv), deny -X POST/PUT/DELETE, deny -d/--data/--json/-F/-T without -X, deny `--request=DELETE`, deny wget --post-data, deny httpie `http PUT host`, deny python requests.post to host, allow commands not naming host, host from OSMON_URL and OSMON_GUARD_HOSTS, malformed JSON naming host → deny. Exit 2 + stderr reason on deny. Depends on 1.

4. [x] **Runbook skill** (medium). Write SKILL.md and `reference/*.md` from SPEC Appendix A and B. Generic examples only (SPEC constraint 1). Depends on 2 (script interface).

5. [x] **Agent + Grafana MCP** (light). `agents/opensearch-monitor.md` per SPEC; `.mcp.json` per SPEC "Grafana / Vector". Depends on 4.

6. [x] **Status band mod** (careful). SPEC "Status band mod" + Appendix C, using the plugin-authoring API (types file path given in brief). `*.test.ts` covering the listed cases; `claude plugin validate` + `claude plugin test`. Depends on 1, 2.

7. [x] **Docs + scrub** (medium). Root README (what it is, install, config per OS, read-only guarantee + its limits: guard covers Bash only and only named hosts; band is early-access), plugin READMEs, `tests/scrub.sh` per SPEC DoD; run scrub, validate, tests; report results. Depends on 2–6.
