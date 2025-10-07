#!/usr/bin/env bash
set -euo pipefail

bump_version() {
	local new_version="$1"
	local pyproject="pyproject.toml"

	perl -0pi -e '
      my $v = $ARGV[0];
      s/(\[project\][^\[]*?version\s*=\s*")([^"]+)(")/${1}$v$3/s
    ' "$new_version" "$pyproject"
}

if [[ $# -lt 1 ]]; then
	echo "Usage: $0 <major|minor|patch|prepatch|prerelease|<version>>" >&2
	exit 1
fi

VERSION="$1"

# Bump version in pyproject.toml
bump_version "$VERSION" # e.g. VERSION="1.1.0"

# Regenerate CHANGELOG.md from git history
python3 scripts/gen_changelog.py

# Stage and commit
git add pyproject.toml CHANGELOG.md || true
git commit -m "chore(release): v${VERSION}"

# Create tag
git tag "v${VERSION}"

echo "Release prepared: v${VERSION}"
echo "Next steps: git push && git push --tags"
