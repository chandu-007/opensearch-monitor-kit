#!/usr/bin/env bash
# Runs every tests/*_test.sh with bash and every tests/*_test.py with python3.
# Prints PASS/FAIL per file; exits non-zero if any file fails.
# Compatible with macOS bash 3.2 (no nullglob reliance).

here=$(cd "$(dirname "$0")" && pwd)
failed=0
ran=0

for f in "$here"/*_test.sh "$here"/*_test.py; do
  [ -e "$f" ] || continue
  case $f in
    *.sh) runner=bash ;;
    *.py) runner=python3 ;;
  esac
  ran=$((ran + 1))
  name=${f##*/}
  if out=$("$runner" "$f" 2>&1); then
    printf 'PASS %s\n' "$name"
  else
    printf 'FAIL %s\n' "$name"
    printf '%s\n' "$out" | sed 's/^/    /'
    failed=$((failed + 1))
  fi
done

if [ "$ran" -eq 0 ]; then
  echo "no tests found in $here"
  exit 1
fi

printf '%d file(s), %d failed\n' "$ran" "$failed"
[ "$failed" -eq 0 ]
