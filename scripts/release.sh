#!/bin/bash
# Publishes a new Bolo version. Running Bolos offer it in their menu within 6 hours.
#   scripts/release.sh 0.2.0
# Before running, add a "## 0.2.0 (YYYY-MM-DD)" section to CHANGELOG.md with what changed.
set -euo pipefail
cd "$(dirname "$0")/.."

die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

version="${1:-}"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "Usage: scripts/release.sh X.Y.Z"
current="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n1)"
newest="$(printf '%s\n%s\n' "$current" "$version" | sort -V | tail -n1)"
[ "$version" != "$current" ] && [ "$newest" = "$version" ] || die "$version must be newer than $current."
[ "$(git branch --show-current)" = main ] || die "Release from main."
git rev-parse -q --verify "refs/tags/v$version" >/dev/null && die "v$version already exists."
grep -q "^## $version " CHANGELOG.md || die "Add a '## $version ($(date +%F))' section to CHANGELOG.md first."
# Only the changelog may have changes; everything else must be committed.
[ -z "$(git status --porcelain -- . ':!CHANGELOG.md')" ] || die "Commit or stash your other changes first."

uv run ruff check bolo tests scripts
uv run pytest -q

sed -i '' "s/^version = \"$current\"/version = \"$version\"/" pyproject.toml
uv lock --quiet
git add pyproject.toml uv.lock CHANGELOG.md
git commit -q -m "release: $version"
git tag -a "v$version" -m "Bolo $version"
git push -q origin main "v$version"

echo "Released v$version."
echo "Optional: add release notes at https://github.com/soumyaux/bolo/releases/new?tag=v$version"
