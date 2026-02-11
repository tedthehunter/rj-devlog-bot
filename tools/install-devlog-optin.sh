#!/usr/bin/env bash
set -euo pipefail

BOT_OWNER="tedthehunter"
BOT_REPO="rj-devlog-bot"
BOT_WORKFLOW_PATH=".github/workflows/linkedin-devlog-reusable.yml"
BOT_REF="${BOT_REF:-v2}"   # pin to a tag/sha for stability

TARGET=".github/workflows/linkedin-devlog.yml"

DRY_RUN="${DEVLOG_OPTIN_DRY_RUN:-0}"
DEBUG="${DEVLOG_OPTIN_DEBUG:-0}"

need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1" >&2; exit 1; }; }
need_cmd gh

gh auth status -h github.com >/dev/null 2>&1 || { echo "Run: gh auth login" >&2; exit 1; }

# Detect current repo (owner/name)
REPO_NWO="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || true)"
[[ -n "$REPO_NWO" ]] || { echo "Run this from inside a cloned GitHub repo." >&2; exit 1; }

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
      OPENAI_API_KEY: \${{ secrets.OPENAI_API_KEY }}
YAML

echo "✅ Created/updated $TARGET in $REPO_NWO"

sha10() { python3 - <<'PY' "$1"
import hashlib,sys
print(hashlib.sha256(sys.argv[1].encode()).hexdigest()[:10])
PY
}

mask() {
  local v="$1"
  local n="${#v}"
  if (( n <= 8 )); then echo "***"; else echo "${v:0:4}…${v: -4}"; fi
}

valid_not_placeholder() {
  local v="$1"
  [[ -n "$v" ]] && [[ "$v" != "-" ]] && [[ "$v" != "REPLACE_ME" ]]
}

validate_author() {
  local v="$1"
  [[ "$v" == urn:li:person:* ]] && (( ${#v} >= 20 ))
}

validate_linkedin_token() {
  local v="$1"
  # allow JWT-like or opaque; just enforce "not tiny" and no whitespace
  valid_not_placeholder "$v" || return 1
  (( ${#v} >= 40 )) || return 1
  [[ "$v" != *" "* ]] || return 1
}

validate_openai_key() {
  local v="$1"
  valid_not_placeholder "$v" || return 1
  (( ${#v} >= 20 )) || return 1
  [[ "$v" == sk-* ]] || [[ "$v" == *"sk-"* ]] || return 1
}

prompt_secret() {
  local var="$1"
  local label="$2"
  local val="${!var:-}"

  if [[ -n "$val" ]]; then return 0; fi
  if [[ -t 0 ]]; then
    read -r -s -p "Enter ${label} (blank to skip): " val || true
    echo
    printf -v "$var" "%s" "$val"
  fi
}

set_secret() {
  local name="$1"
  local value="$2"
  local validator="$3"

  if [[ -z "$value" ]]; then
    [[ "$DEBUG" == "1" ]] && echo "ℹ️  ${name}: skipped (blank)"
    return 0
  fi

  if ! "$validator" "$value"; then
    echo "⚠️  ${name}: looks invalid (len=${#value}, sha10=$(sha10 "$value")). Not setting." >&2
    return 0
  fi

  if [[ "$DEBUG" == "1" ]]; then
    echo "🔎 ${name}: len=${#value} sha10=$(sha10 "$value") sample=$(mask "$value")"
  fi

  if [[ "$DRY_RUN" == "1" ]]; then
    echo "🧪 DRY RUN: would set ${name}"
    return 0
  fi

  printf %s "$value" | gh secret set "$name" -R "$REPO_NWO" -b-
  echo "🔐 Set secret: ${name}"
}

echo
read -r -p "Also set/update secrets for this repo now? (y/N): " DO_SECRETS
if [[ "${DO_SECRETS:-}" =~ ^[Yy]$ ]]; then
  # Pull from env if you exported them; otherwise prompt (you’ll paste from :contentReference[oaicite:1]{index=1}).
  prompt_secret LINKEDIN_AUTHOR_URN    "LINKEDIN_AUTHOR_URN (urn:li:person:...)"
  prompt_secret LINKEDIN_ACCESS_TOKEN  "LINKEDIN_ACCESS_TOKEN (OAuth access_token)"
  prompt_secret OPENAI_API_KEY         "OPENAI_API_KEY (optional)"

  # Optional legacy/unused fields (safe to keep declared)
  prompt_secret LINKEDIN_REFRESH_TOKEN "LINKEDIN_REFRESH_TOKEN (optional)"
  prompt_secret LINKEDIN_CLIENT_ID     "LINKEDIN_CLIENT_ID (optional)"
  prompt_secret LINKEDIN_CLIENT_SECRET "LINKEDIN_CLIENT_SECRET (optional)"

  set_secret LINKEDIN_AUTHOR_URN    "${LINKEDIN_AUTHOR_URN:-}"    validate_author
  set_secret LINKEDIN_ACCESS_TOKEN  "${LINKEDIN_ACCESS_TOKEN:-}"  validate_linkedin_token
  set_secret OPENAI_API_KEY         "${OPENAI_API_KEY:-}"         validate_openai_key

  set_secret LINKEDIN_REFRESH_TOKEN "${LINKEDIN_REFRESH_TOKEN:-}" valid_not_placeholder
  set_secret LINKEDIN_CLIENT_ID     "${LINKEDIN_CLIENT_ID:-}"     valid_not_placeholder
  set_secret LINKEDIN_CLIENT_SECRET "${LINKEDIN_CLIENT_SECRET:-}" valid_not_placeholder

  echo "✅ Secrets processed for ${REPO_NWO}"
else
  echo "Skipping secrets."
fi

echo
echo "Next:"
echo "  git add $TARGET"
echo "  git commit -m 'Enable LinkedIn devlog'"
echo "  git push"