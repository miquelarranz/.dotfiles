#!/usr/bin/env bash
# PostToolUse(Write|Edit|MultiEdit) hook: when Claude writes a note under the vault's Research
# folder, register it with the Herdr research-viewer. Silent otherwise; never blocks a tool call.
set -u
command -v jq >/dev/null || exit 0
input="$(cat)"
file="$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty')"
sid="$(printf '%s' "$input" | jq -r '.session_id // empty')"
case "$file" in
  "$HOME"/Desktop/Vault/Research/*.md) CLAUDE_SESSION_ID="$sid" "$(dirname "$0")/open-in-herdr.sh" "$file" ;;
esac
exit 0
