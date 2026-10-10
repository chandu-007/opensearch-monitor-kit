#!/usr/bin/env bash
# scrub.sh: repo-wide check for internal data before a public push.
#
# Usage: bash tests/scrub.sh [--extra <file>]
#
# Scans every git-tracked file plus untracked, non-ignored files
# (git ls-files -co --exclude-standard). Binary files are skipped.
# Prints file:line: <kind>: <match> for each hit and exits 1 if there is any.
#
# Generic patterns (no internal terms live in this file):
#   ipv4      dotted quads, except documentation ranges 192.0.2.x,
#             198.51.100.x, 203.0.113.x and 127.0.0.1 / 0.0.0.0.
#             Pragmatic version-string filter: a match is ignored when it is
#             immediately preceded by a letter (includes "v1.2.3.4"), a digit
#             or dot, or a single "/" (lib/1.2.3.4; "//" as in http://10.x is
#             still checked), or followed by "." plus a digit or by a digit.
#   email     addresses, except those at example.com / example.org (and their
#             subdomains, RFC 2606) and noreply@anthropic.com; a match whose
#             local part starts with "-" is a CLI flag (curl -d@body.json)
#   acct12    standalone 12-digit numbers (AWS-account-like); not adjacent to
#             a letter, digit, "." or "_"
#   pem       a PEM armour line: five dashes, BEGIN, a space, a capital letter
#   password  "password=" (any case) with a value that is not a placeholder.
#             Placeholders: empty; starting with $ < { * % or a quote
#             followed by one of those; or one of the words x/xx/xxx...,
#             changeme, example, placeholder, redacted, secret, dummy, fake,
#             test, fromenv, "..."
#
# --extra <file>: one term per line (blank lines and lines starting with #
# are ignored), matched case-insensitively as plain substrings. Keep that file
# outside the repo, e.g. ~/private-terms.txt.
#
# bash 3.2 compatible; uses only git, grep and awk (BSD or GNU).

set -u

extra=""
while [ $# -gt 0 ]; do
  case "$1" in
    --extra)
      [ $# -ge 2 ] || { echo "scrub.sh: --extra needs a file" >&2; exit 2; }
      extra="$2"; shift 2 ;;
    --extra=*) extra="${1#--extra=}"; shift ;;
    -h|--help) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "scrub.sh: unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [ -n "$extra" ] && [ ! -r "$extra" ]; then
  echo "scrub.sh: cannot read extra terms file: $extra" >&2
  exit 2
fi

root=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "scrub.sh: not inside a git work tree" >&2; exit 2; }
cd "$root" || exit 2

files=0
hits=0
out=$(
  git ls-files -co --exclude-standard -z | tr '\0' '\n' | while IFS= read -r f; do
    [ -f "$f" ] || continue
    # skip binary files (grep -I treats them as non-matching)
    if [ -s "$f" ] && ! grep -Iq . "$f" 2>/dev/null; then continue; fi
    echo "FILE	$f"
    awk -v extrafile="$extra" -v fname="$f" '
      BEGIN {
        n_extra = 0
        if (extrafile != "") {
          while ((getline t < extrafile) > 0) {
            sub(/\r$/, "", t)
            if (t ~ /^[ \t]*$/ || t ~ /^#/) continue
            extras[++n_extra] = tolower(t)
          }
          close(extrafile)
        }
        oct = "[0-9][0-9]?[0-9]?"
        ipre = oct "\\." oct "\\." oct "\\." oct
        d = "[0-9]"
        acre = d d d d d d d d d d d d
        emre = "[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\\.[A-Za-z0-9-]+)*\\.[A-Za-z][A-Za-z]+"
        pwre = "[Pp][Aa][Ss][Ss][Ww][Oo][Rr][Dd]="
        pemre = "-----BEGIN [A-Z]"
      }
      function hit(kind, m) {
        printf "HIT\t%s:%d: %s: %s\n", fname, FNR, kind, m
      }
      function before(s, i) { return (i > 1) ? substr(s, i - 1, 1) : "" }
      function after(s, i, len) { return substr(s, i + len, 1) }
      function after2(s, i, len) { return substr(s, i + len, 2) }
      function ip_ok(m,   p) {
        split(m, p, ".")
        if (p[1] > 255 || p[2] > 255 || p[3] > 255 || p[4] > 255) return 1
        if (m ~ /^192\.0\.2\./ || m ~ /^198\.51\.100\./ || m ~ /^203\.0\.113\./) return 1
        if (m == "127.0.0.1" || m == "0.0.0.0") return 1
        return 0
      }
      function pw_placeholder(v,   lv) {
        sub(/^["\047]/, "", v)
        sub(/["\047]$/, "", v)
        if (v == "") return 1
        if (v ~ /^[$<{*%]/) return 1
        lv = tolower(v)
        if (lv ~ /^x+$/ || lv ~ /^\.\.\.+$/) return 1
        if (lv ~ /^(changeme|example|placeholder|redacted|secret|dummy|fake|test|fromenv)$/) return 1
        return 0
      }
      {
        line = $0
        # ipv4
        s = line; off = 0
        while (match(s, ipre)) {
          i = off + RSTART; m = substr(line, i, RLENGTH)
          b = before(line, i); a = after(line, i, RLENGTH); a2 = after2(line, i, RLENGTH)
          b2 = (i > 2) ? substr(line, i - 2, 2) : ""
          skip = (b ~ /[A-Za-z0-9.]/ || (b == "/" && b2 != "//") || a ~ /[0-9]/ || a2 ~ /^\.[0-9]$/)
          if (!(skip || ip_ok(m))) hit("ipv4", m)
          off = i + RLENGTH - 1; s = substr(line, off + 1)
        }
        # email
        s = line; off = 0
        while (match(s, emre)) {
          i = off + RSTART; m = substr(line, i, RLENGTH)
          lm = tolower(m)
          if (!(lm ~ /[@.]example\.(com|org)$/ || lm == "noreply@anthropic.com" || m ~ /^-/)) hit("email", m)
          off = i + RLENGTH - 1; s = substr(line, off + 1)
        }
        # 12-digit numbers
        s = line; off = 0
        while (match(s, acre)) {
          i = off + RSTART; m = substr(line, i, RLENGTH)
          b = before(line, i); a = after(line, i, RLENGTH)
          if (!(b ~ /[A-Za-z0-9._]/ || a ~ /[A-Za-z0-9._]/)) hit("acct12", m)
          off = i + RLENGTH - 1; s = substr(line, off + 1)
        }
        # PEM armour
        if (match(line, pemre)) hit("pem", substr(line, RSTART, 16))
        # password=
        s = line; off = 0
        while (match(s, pwre)) {
          i = off + RSTART + RLENGTH; rest = substr(line, i)
          v = rest
          if (match(v, /[ \t;&|)`,]/)) v = substr(v, 1, RSTART - 1)
          if (!pw_placeholder(v)) hit("password", "password=" v)
          off = i - 1; s = substr(line, off + 1)
        }
        # extra terms
        if (n_extra > 0) {
          ll = tolower(line)
          for (k = 1; k <= n_extra; k++)
            if (index(ll, extras[k]) > 0) hit("extra", "term #" k)
        }
      }
    ' "$f"
  done
)

files=$(printf '%s\n' "$out" | grep -c '^FILE	' || true)
hits=$(printf '%s\n' "$out" | grep -c '^HIT	' || true)
printf '%s\n' "$out" | grep '^HIT	' | sed 's/^HIT	//'

if [ "$hits" -gt 0 ]; then
  echo "scrub: FAIL: $hits hit(s) in $files file(s) scanned"
  exit 1
fi
echo "scrub: OK: $files file(s) scanned, no hits"
exit 0
