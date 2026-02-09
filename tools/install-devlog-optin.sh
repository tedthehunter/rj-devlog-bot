#!/usr/bin/env bash
set -euo pipefail

# --- Config (your bot repo) ---
BOT_OWNER="tedthehunter"
BOT_REPO="rj-devlog-bot"
BOT_WORKFLOW_PATH=".github/workflows/linkedin-devlog-reusable.yml"
BOT_REF="v1"   # tag/branch/SHA in the bot repo

TARGET=".github/workflows/linkedin-devlog.yml"

need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1"; exit 1; }; }
need_cmd gh

# Ensure gh is authenticated
gh auth status >/dev/null 2>&1 || {
  echo "GitHub CLI not authenticated. Run: gh auth login"
  exit 1
}

# Detect current repo (owner/name)
REPO_NWO="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || true)"
if [[ -z "${REPO_NWO}" ]]; then
  echo "Could not detect the current repo. Run this from inside a cloned GitHub repo."
  exit 1
fi

mkdir -p .github/workflows

cat > "$TARGET" <<YAML
name: LinkedIn Devlog

on:
  push:
    branches: ["main", "master"]
    paths-ignore:
      - "**/*.md"
      - "docs/**"

jobs:
  devlog:
    uses: ${BOT_OWNER}/${BOT_REPO}/${BOT_WORKFLOW_PATH}@${BOT_REF}
    secrets:
      LINKEDIN_AUTHOR_URN: \${{ secrets.LINKEDIN_AUTHOR_URN }}
      LINKEDIN_ACCESS_TOKEN: \${{ secrets.LINKEDIN_ACCESS_TOKEN }}
      LINKEDIN_REFRESH_TOKEN: \${{ secrets.LINKEDIN_REFRESH_TOKEN }}
      LINKEDIN_CLIENT_ID: \${{ secrets.LINKEDIN_CLIENT_ID }}
      LINKEDIN_CLIENT_SECRET: \${{ secrets.LINKEDIN_CLIENT_SECRET }}
YAML

echo "✅ Created $TARGET in $REPO_NWO"

set_secret() {
  local name="$1"
  local value="$2"
  if [[ -n "${value}" ]]; then
    printf %s "${value}" | gh secret set "${name}" -R "${REPO_NWO}" -b-
    echo "🔐 Set secret: ${name}"
  fi
}

prompt_secret() {
  local var="$1"
  local label="$2"
  if [[ -z "${!var:-}" ]]; then
    read -r -s -p "Enter ${label} (leave blank to skip): " tmp || true
    echo
    printf -v "$var" "%s" "${tmp}"
  fi
}

echo
read -r -p "Also set LinkedIn secrets for this repo now? (y/N): " DO_SECRETS
if [[ "${DO_SECRETS}" =~ ^[Yy]$ ]]; then
  # Prefer env vars if you already exported them; otherwise prompt securely.
  prompt_secret LINKEDIN_AUTHOR_URN   "LINKEDIN_AUTHOR_URN (urn:li:person:...)"
  prompt_secret LINKEDIN_ACCESS_TOKEN "LINKEDIN_ACCESS_TOKEN (optional if using refresh)"
  prompt_secret LINKEDIN_REFRESH_TOKEN "LINKEDIN_REFRESH_TOKEN (if you have one)"
  prompt_secret LINKEDIN_CLIENT_ID     "LINKEDIN_CLIENT_ID (if using refresh)"
  prompt_secret LINKEDIN_CLIENT_SECRET "LINKEDIN_CLIENT_SECRET (if using refresh)"

  set_secret LINKEDIN_AUTHOR_URN   "${LINKEDIN_AUTHOR_URN:-}"
  set_secret LINKEDIN_ACCESS_TOKEN "${LINKEDIN_ACCESS_TOKEN:-}"
  set_secret LINKEDIN_REFRESH_TOKEN "${LINKEDIN_REFRESH_TOKEN:-}"
  set_secret LINKEDIN_CLIENT_ID     "${LINKEDIN_CLIENT_ID:-}"
  set_secret LINKEDIN_CLIENT_SECRET "${LINKEDIN_CLIENT_SECRET:-}"

  echo "✅ Secrets updated for ${REPO_NWO}"
else
  echo "Skipping secrets."
fi

echo
echo "Next:"
echo "  git add $TARGET"
echo "  git commit -m 'Enable LinkedIn devlog'"
echo "  git push"
