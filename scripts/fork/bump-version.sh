#!/usr/bin/env bash
# Set the release version everywhere it lives, so a bump cannot be half done:
# apps/desktop/package.json, pyproject.toml, the desktop workspace entry in package-lock.json and uv.lock (via `uv lock`).
#
#   scripts/fork/bump-version.sh 0.2.0
#
# Then open a PR. When it merges to main, .github/workflows/tag-release.yml tags v0.2.0 and the release build publishes it.
# Docs: docs/private-fork/RELEASING.md.
set -euo pipefail

cd "$(dirname "$0")/../.."
new="${1:-}"
[[ "$new" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "usage: $0 MAJOR.MINOR.PATCH" >&2; exit 2; }
current="$(python3 -c 'import json; print(json.load(open("apps/desktop/package.json"))["version"])')"
if [ "$(printf '%s\n%s\n' "$current" "$new" | sort -V | tail -1)" != "$new" ] || [ "$current" = "$new" ]; then
  echo "bump-version: $new is not newer than the current version $current" >&2
  exit 1
fi

python3 - "$new" <<'PY'
import re, sys
new = sys.argv[1]
def sub(path, pattern, repl, flags=0):
    text = open(path, encoding="utf-8").read()
    updated, count = re.subn(pattern, repl, text, count=1, flags=flags)
    if count != 1:
        sys.exit(f"bump-version: could not find the version in {path}")
    open(path, "w", encoding="utf-8", newline="\n").write(updated)
sub("apps/desktop/package.json", r'("version": ")[^"]+(")', rf'\g<1>{new}\g<2>')
sub("pyproject.toml", r'(?m)^(version = ")[^"]+(")$', rf'\g<1>{new}\g<2>')
# the desktop workspace's own entry in the npm lockfile ("apps/desktop": { "name": "hermes", "version": ... })
sub("package-lock.json", r'("apps/desktop": \{\s+"name": "hermes",\s+"version": ")[^"]+(")', rf'\g<1>{new}\g<2>', re.S)
PY

if command -v uv >/dev/null 2>&1; then
  uv lock
else
  echo "bump-version: uv not found; run 'hermes pm lock' (or uv lock) so uv.lock gets the new version" >&2
fi
echo "bump-version: $current -> $new in apps/desktop/package.json, pyproject.toml, package-lock.json$(command -v uv >/dev/null 2>&1 && echo ', uv.lock')"
