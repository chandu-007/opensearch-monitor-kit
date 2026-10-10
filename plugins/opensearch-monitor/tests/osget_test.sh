#!/usr/bin/env bash
# Tests for skills/opensearch-health/scripts/osget.
# Uses stub `curl` and `security` first on PATH; every case runs under
# `env -i` so the developer's real OSMON_* settings and Keychain are never
# touched. Compatible with macOS bash 3.2.

here=$(cd "$(dirname "$0")" && pwd)
OSGET=${OSGET_UNDER_TEST:-$here/../skills/opensearch-health/scripts/osget}

T=$(mktemp -d "${TMPDIR:-/tmp}/osget-test.XXXXXX") || exit 1
trap 'rm -rf "$T"' EXIT
STUB=$T/bin
LOG=$T/log
mkdir -p "$STUB" "$T/home" "$T/tmp" "$LOG"

PY_DIR=
if command -v python3 >/dev/null 2>&1; then
  PY_DIR=$(dirname "$(command -v python3)")
fi
BASE_PATH="$STUB:/usr/bin:/bin:/usr/sbin:/sbin${PY_DIR:+:$PY_DIR}"

# ------------------------------------------------------------------ stubs
cat > "$STUB/curl" <<'EOF'
#!/bin/bash
log=$STUB_LOG
: > "$log/argv"
for a in "$@"; do printf '%s\n' "$a" >> "$log/argv"; done
cat > "$log/stdin"
env > "$log/env"
touch "$log/called"
out=
hdr=
w=0
while [ $# -gt 0 ]; do
  case $1 in
    -o) out=$2; shift ;;
    -D) hdr=$2; shift ;;
    -w) w=1; shift ;;
    --data-binary)
      f=${2#@}
      printf '%s\n' "$f" > "$log/body_path"
      cp "$f" "$log/body_sent"
      (stat -c %a "$f" 2>/dev/null || stat -f %Lp "$f") > "$log/body_mode"
      shift ;;
  esac
  shift
done
if [ "${STUB_EXIT:-0}" -ne 0 ]; then
  echo "curl: ($STUB_EXIT) stub failure" >&2
  exit "$STUB_EXIT"
fi
[ -n "$out" ] && printf '%s' "${STUB_BODY-}" > "$out"
if [ -n "$hdr" ]; then
  printf 'HTTP/1.1 %s Stub\r\nContent-Type: application/json\r\n' "${STUB_STATUS:-200}" > "$hdr"
  [ -n "${STUB_LOCATION-}" ] && printf 'location: %s\r\n' "$STUB_LOCATION" >> "$hdr"
  printf '\r\n' >> "$hdr"
fi
[ "$w" -eq 1 ] && printf '%s' "${STUB_STATUS:-200}"
exit 0
EOF

cat > "$STUB/security" <<'EOF'
#!/bin/bash
: > "$STUB_LOG/sec_argv"
for a in "$@"; do printf '%s\n' "$a" >> "$STUB_LOG/sec_argv"; done
# The keychain secret comes from a file so it is not in osget's environment.
if [ -n "${STUB_KEYCHAIN_FILE-}" ]; then
  cat "$STUB_KEYCHAIN_FILE"
  exit 0
fi
echo "security: The specified item could not be found in the keychain." >&2
exit 44
EOF
chmod 755 "$STUB/curl" "$STUB/security"

# ---------------------------------------------------------------- fixtures
PW='Zq9"pa\ss'                       # contains a quote and a backslash
PW_ESC='Zq9\"pa\\ss'                 # curl-config escaped form
PW_MARK=Zq9                          # substring that must never leak
URL=https://opensearch.example.com

printf '%s\n' "$PW" > "$T/pw600"; chmod 600 "$T/pw600"
printf '%s\n' "$PW" > "$T/pw400"; chmod 400 "$T/pw400"
printf '%s\n' "$PW" > "$T/pw644"; chmod 644 "$T/pw644"
ln -s pw600 "$T/pwlink600"; ln -s pw644 "$T/pwlink644"
printf '%s' '{"query":{"match_all":{}}}' > "$T/req.json"
printf '%s\n' "$PW" > "$T/kc_pw"
printf '%s\n' fromkeychain > "$T/kc_from"
printf '%s\n' other > "$T/kc_other"

# ---------------------------------------------------------------- helpers
FAILS=0
CHECKS=0
CASE=

fail() { FAILS=$((FAILS + 1)); printf 'FAIL [%s] %s\n' "$CASE" "$*"; }
ok() { CHECKS=$((CHECKS + 1)); }

# run_osget [VAR=value ...] -- [osget args ...]
run_osget() {
  envs=()
  while [ $# -gt 0 ] && [ "$1" != -- ]; do envs+=("$1"); shift; done
  [ $# -gt 0 ] && shift
  rm -rf "$LOG"; mkdir -p "$LOG"
  env -i PATH="$BASE_PATH" HOME="$T/home" TMPDIR="$T/tmp" STUB_LOG="$LOG" \
    ${envs[@]+"${envs[@]}"} bash "$OSGET" "$@" > "$T/out" 2> "$T/err"
  RC=$?
}

# Default working configuration (password file, mode 600).
std() { run_osget OSMON_URL=$URL OSMON_USER=monitor OSMON_PASSWORD_FILE="$T/pw600" "$@"; }

assert_rc() {
  if [ "$RC" -eq "$1" ]; then ok; else fail "exit $RC, want $1; stderr: $(cat "$T/err")"; fi
}
assert_has() { # file needle
  if grep -qF -- "$2" "$1" 2>/dev/null; then ok; else fail "${1##*/} lacks: $2"; fi
}
assert_lacks() { # file needle
  if grep -qF -- "$2" "$1" 2>/dev/null; then fail "${1##*/} contains: $2"; else ok; fi
}
assert_called() {
  if [ -e "$LOG/called" ]; then ok; else fail "curl was not called"; fi
}
assert_not_called() {
  if [ -e "$LOG/called" ]; then fail "curl was called"; else ok; fi
}
assert_seq() { # argv contains consecutive lines $1 then $2
  if awk -v a="$1" -v b="$2" 'NR>1 && prev==a && $0==b {f=1} {prev=$0} END{exit !f}' "$LOG/argv"; then ok
  else fail "argv lacks sequence: $1 $2"; fi
}
assert_eq() { # actual expected label
  if [ "$1" = "$2" ]; then ok; else fail "$3: got [$1], want [$2]"; fi
}
assert_tmp_empty() {
  if [ -z "$(ls -A "$T/tmp")" ]; then ok; else fail "temp files left: $(ls -A "$T/tmp")"; fi
}
# Method is GET and nothing else can change it.
assert_get_only() {
  assert_eq "$(sed -n 1p "$LOG/argv")" "-q" "first curl arg"
  assert_seq -X GET
  assert_eq "$(grep -cE '^(-X|--request)' "$LOG/argv")" 1 "method flag count"
  if grep -qxE -- '-d|--data|--data-raw|--data-ascii|--data-urlencode|--json|-F|--form|--form-string|-T|--upload-file|-G|--get|-I|--head|-L|--location' "$LOG/argv"; then
    fail "argv has a forbidden flag: $(grep -xE -- '-d|--data|--data-raw|--data-ascii|--data-urlencode|--json|-F|--form|--form-string|-T|--upload-file|-G|--get|-I|--head|-L|--location' "$LOG/argv" | tr '\n' ' ')"
  else ok; fi
}
# The password never reaches argv or curl's environment.
assert_no_leak() {
  assert_lacks "$LOG/argv" "$PW_MARK"
  assert_lacks "$LOG/env" "$PW_MARK"
  assert_lacks "$LOG/env" "OSMON_PASSWORD="
  [ -e "$LOG/sec_argv" ] && assert_lacks "$LOG/sec_argv" "$PW_MARK"
  assert_lacks "$T/err" "$PW_MARK"
}

# ------------------------------------------------------------------ cases
CASE="usage"
run_osget --; assert_rc 2
run_osget -- --help; assert_rc 0; assert_has "$T/out" "Usage:"
std -- _cat/nodes --body '{}' --body-file "$T/req.json"; assert_rc 2; assert_not_called
std -- --check _cat/nodes; assert_rc 2; assert_not_called
std -- a b; assert_rc 2; assert_not_called

CASE="method cannot be changed"
std -- -X POST _cluster/health; assert_rc 2; assert_not_called
std -- --request=DELETE _cluster/health; assert_rc 2; assert_not_called
std -- _cluster/health -d '{}'; assert_rc 2; assert_not_called
std OSMON_METHOD=POST REQUEST_METHOD=DELETE CURL_HOME="$T/home" -- _cluster/health
assert_rc 0; assert_get_only

CASE="basic GET"
std STUB_BODY='{"status":"green"}' -- _cluster/health
assert_rc 0; assert_called; assert_get_only; assert_no_leak
assert_eq "$(cat "$T/out")" '{"status":"green"}' "stdout"
assert_eq "$(tail -n 1 "$LOG/argv")" "$URL/_cluster/health" "url"
assert_seq -K -
assert_seq -H 'Accept: application/json'
assert_seq --max-time 60
if grep -qx -- -k "$LOG/argv"; then fail "-k present without OSMON_INSECURE"; else ok; fi
assert_eq "$(cat "$LOG/stdin")" "user = \"monitor:$PW_ESC\"" "curl config on stdin"
assert_tmp_empty

CASE="leading slash, trailing slash in URL, empty path"
run_osget OSMON_URL=$URL/ OSMON_USER=monitor OSMON_PASSWORD_FILE="$T/pw600" -- /_cat/nodes?v
assert_rc 0; assert_eq "$(tail -n 1 "$LOG/argv")" "$URL/_cat/nodes?v" "url"
std -- ""; assert_rc 0; assert_eq "$(tail -n 1 "$LOG/argv")" "$URL/" "url"
std -- /; assert_rc 0; assert_eq "$(tail -n 1 "$LOG/argv")" "$URL/" "url"

CASE="--raw, insecure, timeout"
std OSMON_INSECURE=1 OSMON_TIMEOUT=5 -- _cat/indices --raw
assert_rc 0; assert_get_only
assert_lacks "$LOG/argv" "Accept: application/json"
assert_seq --max-time 5
if grep -qx -- -k "$LOG/argv"; then ok; else fail "-k missing with OSMON_INSECURE=1"; fi
std OSMON_TIMEOUT=abc -- _cat/indices; assert_rc 3; assert_not_called

CASE="path validation"
for p in "https://evil.example.com/x" "HTTP://evil.example.com/x" "http:x" \
         "//evil.example.com/x" "_cat/../x" ".." "_cat/nodes v" \
         "$(printf '_cat/nodes\tv')" "$(printf '_cat\nnodes')" "$(printf '_cat\001x')"; do
  std -- "$p"; assert_rc 2; assert_not_called
done

CASE="--body"
BODY='{"query":{"match_all":{}}}'
std STUB_BODY='{"hits":{}}' -- _plugins/_alerting/monitors/_search --body "$BODY"
assert_rc 0; assert_get_only; assert_no_leak
assert_seq -H 'Content-Type: application/json'
assert_eq "$(cat "$LOG/body_sent")" "$BODY" "body sent"
assert_eq "$(cat "$LOG/body_mode")" 600 "body temp file mode"
bp=$(cat "$LOG/body_path")
assert_seq --data-binary "@$bp"
if [ -e "$bp" ]; then fail "body temp file not removed: $bp"; else ok; fi
assert_tmp_empty

CASE="--body-file"
std -- _plugins/_alerting/monitors/_search --body-file "$T/req.json"
assert_rc 0; assert_get_only
assert_seq --data-binary "@$T/req.json"
assert_eq "$(cat "$LOG/body_sent")" "$(cat "$T/req.json")" "body sent"
std -- _search --body-file -; assert_rc 2; assert_not_called
std -- _search --body-file "$T/nope.json"; assert_rc 2; assert_not_called

CASE="password file permissions"
run_osget OSMON_URL=$URL OSMON_USER=monitor OSMON_PASSWORD_FILE="$T/pw644" -- _cluster/health
assert_rc 3; assert_not_called; assert_has "$T/err" "chmod 600 $T/pw644"
run_osget OSMON_URL=$URL OSMON_USER=monitor OSMON_PASSWORD_FILE="$T/pw400" -- _cluster/health
assert_rc 0; assert_eq "$(cat "$LOG/stdin")" "user = \"monitor:$PW_ESC\"" "curl config"
run_osget OSMON_URL=$URL OSMON_USER=monitor OSMON_PASSWORD_FILE="$T/pwlink600" -- _cluster/health
assert_rc 0; assert_eq "$(cat "$LOG/stdin")" "user = \"monitor:$PW_ESC\"" "symlinked 600 file"
run_osget OSMON_URL=$URL OSMON_USER=monitor OSMON_PASSWORD_FILE="$T/pwlink644" -- _cluster/health
assert_rc 3; assert_not_called
run_osget OSMON_URL=$URL OSMON_USER=monitor OSMON_PASSWORD_FILE="$T/missing" -- _cluster/health
assert_rc 3; assert_not_called

CASE="missing config / credentials"
run_osget OSMON_URL=$URL OSMON_USER=monitor -- _cluster/health
assert_rc 3; assert_not_called
assert_has "$T/err" "security add-generic-password -a monitor -s osmon -w"
assert_has "$T/err" "chmod 600 <file>"
assert_has "$T/err" "OSMON_PASSWORD_FILE"
run_osget OSMON_USER=monitor OSMON_PASSWORD_FILE="$T/pw600" -- _cluster/health
assert_rc 3; assert_not_called
run_osget OSMON_URL=ftp://opensearch.example.com OSMON_USER=monitor OSMON_PASSWORD_FILE="$T/pw600" -- _cluster/health
assert_rc 3; assert_not_called
run_osget OSMON_URL=$URL OSMON_PASSWORD_FILE="$T/pw600" -- _cluster/health
assert_rc 3; assert_not_called

CASE="keychain"
run_osget OSMON_URL=$URL OSMON_USER=monitor STUB_KEYCHAIN_FILE="$T/kc_pw" -- _cluster/health
assert_rc 0; assert_get_only; assert_no_leak
assert_eq "$(tr '\n' ' ' < "$LOG/sec_argv")" "find-generic-password -a monitor -s osmon -w " "security argv"
assert_eq "$(cat "$LOG/stdin")" "user = \"monitor:$PW_ESC\"" "curl config"
run_osget OSMON_URL=$URL OSMON_USER=monitor OSMON_KEYCHAIN_SERVICE=osmon-prod STUB_KEYCHAIN_FILE="$T/kc_other" -- _cluster/health
assert_rc 0
assert_eq "$(tr '\n' ' ' < "$LOG/sec_argv")" "find-generic-password -a monitor -s osmon-prod -w " "security argv"

CASE="lookup order"
run_osget OSMON_URL=$URL OSMON_USER=monitor OSMON_PASSWORD_FILE="$T/pw600" STUB_KEYCHAIN_FILE="$T/kc_from" OSMON_PASSWORD=fromenv -- _cluster/health
assert_rc 0; assert_eq "$(cat "$LOG/stdin")" "user = \"monitor:$PW_ESC\"" "file wins"
if [ -e "$LOG/sec_argv" ]; then fail "security consulted although file set"; else ok; fi
run_osget OSMON_URL=$URL OSMON_USER=monitor STUB_KEYCHAIN_FILE="$T/kc_from" OSMON_PASSWORD=fromenv -- _cluster/health
assert_rc 0; assert_eq "$(cat "$LOG/stdin")" 'user = "monitor:fromkeychain"' "keychain beats env"
assert_lacks "$LOG/env" "fromenv"

CASE="env password"
run_osget OSMON_URL=$URL OSMON_USER=monitor OSMON_PASSWORD="$PW" _og_pw=preset _og_cfg=preset _og_env_pw=preset _esc=preset _in=preset _out=preset -- _cluster/health
assert_rc 0; assert_get_only; assert_no_leak
assert_eq "$(cat "$LOG/stdin")" "user = \"monitor:$PW_ESC\"" "curl config"

CASE="HTTP error"
std STUB_STATUS=500 STUB_BODY='{"error":"boom"}' -- _cluster/health
assert_rc 4; assert_has "$T/out" '{"error":"boom"}'; assert_has "$T/err" "HTTP 500"
std STUB_STATUS=404 STUB_BODY='{"error":"no such index"}' -- missing-index/_search
assert_rc 4; assert_has "$T/out" "no such index"
assert_tmp_empty

CASE="redirect not followed"
std STUB_STATUS=302 STUB_LOCATION=https://login.example.com/sso STUB_BODY='{"moved":true}' -- _cluster/health
assert_rc 4; assert_get_only
assert_has "$T/out" '{"moved":true}'
assert_has "$T/err" "redirect not followed (Location: https://login.example.com/sso)"
assert_tmp_empty
std STUB_STATUS=301 -- _cluster/health
assert_rc 4; assert_has "$T/err" "redirect not followed (Location: not provided)"
std STUB_STATUS=204 -- _cluster/health
assert_rc 0

CASE="userinfo in OSMON_URL"
for u in "https://a:${PW_MARK}x@opensearch.example.com" "https://${PW_MARK}@opensearch.example.com" \
         "https://a:${PW_MARK}x@opensearch.example.com/" "http://a:${PW_MARK}x@opensearch.example.com:9200/sub"; do
  run_osget OSMON_URL="$u" OSMON_USER=monitor OSMON_PASSWORD_FILE="$T/pw600" -- _cluster/health
  assert_rc 3; assert_not_called; assert_lacks "$T/err" "$PW_MARK"
  assert_has "$T/err" "OSMON_USER"
  run_osget OSMON_URL="$u" OSMON_USER=monitor OSMON_PASSWORD_FILE="$T/pw600" -- --check
  assert_rc 3; assert_not_called; assert_lacks "$T/err" "$PW_MARK"; assert_lacks "$T/out" "$PW_MARK"
done
run_osget OSMON_URL=https://a:b@opensearch.example.com OSMON_USER=monitor OSMON_PASSWORD_FILE="$T/pw600" -- _cluster/health
assert_rc 3; assert_not_called; assert_lacks "$T/err" "a:b"
run_osget OSMON_URL=https://a@opensearch.example.com OSMON_USER=monitor OSMON_PASSWORD_FILE="$T/pw600" -- _cluster/health
assert_rc 3; assert_not_called
# "@" after the authority (in a proxy path) is not userinfo.
run_osget OSMON_URL=https://opensearch.example.com/proxy/@x OSMON_USER=monitor OSMON_PASSWORD_FILE="$T/pw600" -- _cluster/health
assert_rc 0; assert_eq "$(tail -n 1 "$LOG/argv")" "https://opensearch.example.com/proxy/@x/_cluster/health" "url"

CASE="network failure"
std STUB_EXIT=7 -- _cluster/health; assert_rc 5; assert_tmp_empty
std STUB_EXIT=28 -- _cluster/health; assert_rc 5
std STUB_EXIT=35 -- _cluster/health; assert_rc 5

CASE="--check"
ROOT='{"name":"node-1","cluster_name":"acme-logs","version":{"number":"2.17.1"}}'
std STUB_BODY="$ROOT" -- --check
assert_rc 0; assert_get_only
assert_has "$T/out" "config: url=$URL user=monitor password-source=file"
assert_eq "$(tail -n 1 "$LOG/argv")" "$URL/" "check url"
if [ -n "$PY_DIR" ]; then
  assert_has "$T/out" "cluster_name=acme-logs"
  assert_has "$T/out" "version=2.17.1"
fi
assert_lacks "$T/out" "$PW_MARK"; assert_lacks "$T/err" "$PW_MARK"
run_osget OSMON_URL=$URL OSMON_USER=monitor OSMON_PASSWORD="$PW" STUB_BODY="$ROOT" -- --check
assert_rc 0; assert_has "$T/out" "password-source=env"
assert_lacks "$T/out" "$PW_MARK"; assert_lacks "$T/err" "$PW_MARK"
run_osget OSMON_URL=$URL OSMON_USER=monitor STUB_KEYCHAIN_FILE="$T/kc_pw" STUB_BODY="$ROOT" -- --check
assert_rc 0; assert_has "$T/out" "password-source=keychain"
assert_lacks "$T/out" "$PW_MARK"

# ---------------------------------------------------------------- summary
if [ "$FAILS" -gt 0 ]; then
  printf '%d failure(s), %d check(s) passed\n' "$FAILS" "$CHECKS"
  exit 1
fi
printf 'ok: %d checks\n' "$CHECKS"
