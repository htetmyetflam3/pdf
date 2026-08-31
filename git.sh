#!/bin/bash
# daily-commit.sh
# Stages modified files, bumps version in pyproject.toml, commits locally.
# Add --r flag to also push to remote.
# Usage: ./git.sh [--r] [commit-message]

set -e

# --- Parse arguments ---
PUSH_REMOTE=false
CUSTOM_MSG=""

for arg in "$@"; do
    if [ "$arg" = "--r" ]; then
        PUSH_REMOTE=true
    else
        CUSTOM_MSG="$arg"
    fi
done

# Resolve paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"

# Check if we're in a git repo
if [ ! -d "$REPO_ROOT/.git" ]; then
    echo "[ERR] Not a git repository"
    exit 1
fi

cd "$REPO_ROOT"

# Stage modified files
git add -A

# Check if there's anything to commit
if git diff --cached --quiet; then
    echo "[SKIP] No changes to commit"
    exit 0
fi

# --- Bump version in pyproject.toml ---
NEW_VERSION=$(sed -n 's/^version = "\([0-9]*\)\.\([0-9]*\)\.\([0-9]*\)"/\1 \2 \3/p' pyproject.toml | {
    read major minor patch
    patch=$((patch + 1))
    if [ "$patch" -gt 9 ]; then
        patch=0
        minor=$((minor + 1))
    fi
    if [ "$minor" -gt 9 ]; then
        minor=0
        major=$((major + 1))
    fi
    echo "$major.$minor.$patch"
})

sed -i "s/^version = \"[0-9]*\.[0-9]*\.[0-9]*\"/version = \"$NEW_VERSION\"/" pyproject.toml

# --- Dynamic date & time for default message ---
DATETIME=$(date +"%a %I:%M %p")

# Use custom message if provided, otherwise default
MSG="${CUSTOM_MSG:-daily update v$NEW_VERSION — $DATETIME}"

git add pyproject.toml
git commit -m "$MSG"

echo "[OK] Committed: $MSG"
echo "[OK] New version: $NEW_VERSION"

if [ "$PUSH_REMOTE" = true ]; then
    git push
    echo "[OK] Pushed to remote"
fi

git log --oneline -3
