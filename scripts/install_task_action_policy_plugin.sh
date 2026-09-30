#!/usr/bin/env bash
# Deterministic deployment of the Hermes task_action_policy enforcement
# plugin from this repository's tracked canonical sources.
#
# Source-of-truth relationship (established 2026-09-29):
#
#   fazle-mcp/task_action_policy/__init__.py   <-- canonical ENFORCEMENT source
#   fazle-mcp/action_policy.py                 <-- canonical CLASSIFIER source
#          |  (this script: deterministic copy)
#          v
#   ~/.hermes/plugins/task_action_policy/{__init__.py,action_policy.py}
#          |  (Hermes loads the deployed copy)
#          v
#   Hermes pre-tool-call enforcement hook
#
# There is exactly ONE editable enforcement implementation and exactly ONE
# editable classifier implementation. The deployed directory is a derived
# artifact and must never be hand-edited; edit the canonical source here and
# re-run this script.
#
# Usage:
#   install_task_action_policy_plugin.sh           # deploy
#   install_task_action_policy_plugin.sh --check   # fail-closed drift check
#
# --check performs NO writes. It exits non-zero on any drift, and is safe to
# run from a gate or CI step.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="${HERMES_PLUGIN_DIR:-$HOME/.hermes/plugins/task_action_policy}"

# canonical source -> deployed filename
PAIRS=(
  "task_action_policy/__init__.py:__init__.py"
  "action_policy.py:action_policy.py"
)

CHECK_ONLY=0
if [ "${1:-}" = "--check" ]; then
  CHECK_ONLY=1
fi

fail() { echo "TASK_ACTION_POLICY_PLUGIN_FAIL: $*" >&2; exit 1; }

# ── verify every canonical source is actually tracked by git ────────────────
for pair in "${PAIRS[@]}"; do
  src="${REPO_DIR}/${pair%%:*}"
  [ -f "$src" ] || fail "canonical source missing: $src"
  if ! git -C "$REPO_DIR" ls-files --error-unmatch "${pair%%:*}" >/dev/null 2>&1; then
    fail "canonical source is NOT tracked by git: ${pair%%:*}"
  fi
done

# ── check mode: compare only ────────────────────────────────────────────────
if [ "$CHECK_ONLY" -eq 1 ]; then
  rc=0
  for pair in "${PAIRS[@]}"; do
    src="${REPO_DIR}/${pair%%:*}"
    dst="${TARGET_DIR}/${pair##*:}"
    if [ ! -f "$dst" ]; then
      echo "DRIFT: deployed target missing: $dst" >&2
      rc=1
      continue
    fi
    a="$(sha256sum "$src" | cut -d' ' -f1)"
    b="$(sha256sum "$dst" | cut -d' ' -f1)"
    if [ "$a" != "$b" ]; then
      echo "DRIFT: $dst" >&2
      echo "  canonical: $a" >&2
      echo "  deployed : $b" >&2
      rc=1
    else
      echo "OK: ${pair##*:} $a"
    fi
  done
  [ "$rc" -eq 0 ] || fail "deployed plugin does not match canonical source"
  echo "TASK_ACTION_POLICY_PLUGIN_INTEGRITY: PASS"
  exit 0
fi

# ── install mode: deterministic copy ────────────────────────────────────────
mkdir -p "$TARGET_DIR"
for pair in "${PAIRS[@]}"; do
  src="${REPO_DIR}/${pair%%:*}"
  dst="${TARGET_DIR}/${pair##*:}"
  # write-then-verify so a partial copy can never be left behind
  cp "$src" "${dst}.tmp"
  mv -f "${dst}.tmp" "$dst"
  a="$(sha256sum "$src" | cut -d' ' -f1)"
  b="$(sha256sum "$dst" | cut -d' ' -f1)"
  [ "$a" = "$b" ] || fail "post-copy hash mismatch for $dst"
  echo "installed: $dst  $a"
done

echo "TASK_ACTION_POLICY_PLUGIN_INSTALL: PASS"
