#!/usr/bin/env bash
set -euo pipefail

terms_file=.github/denylist.txt
if [[ ! -f "$terms_file" ]]; then
  echo "deny-list: no .github/denylist.txt configured; nothing to check"
  exit 0
fi

status=0
while IFS= read -r term || [[ -n "$term" ]]; do
  [[ -z "$term" || "$term" == \#* ]] && continue
  while IFS= read -r tracked; do
    [[ "$tracked" == "$terms_file" ]] && continue
    if grep -Fqi -- "$term" "$tracked"; then
      printf 'deny-list hit: %s in %s\n' "$term" "$tracked" >&2
      status=1
    fi
  done < <(git ls-files -z | tr '\0' '\n')
done < "$terms_file"
exit "$status"
