#!/usr/bin/env bash
# Register a vault note with the Herdr research-viewer, under the Claude SESSION that wrote it.
# Reveal the panel only when the session first touches the note AND its terminal is the focused
# one in Herdr.
#   usage: CLAUDE_SESSION_ID=<id> open-in-herdr.sh <absolute note path> [--reveal]
set -u
path="${1:-}"; flag="${2:-}"; sid="${CLAUDE_SESSION_ID:-}"; term="${HC_TERMINAL_ID:-}"
[ -n "$path" ] && [ -n "$sid" ] || exit 0
command -v jq >/dev/null && command -v herdr-app >/dev/null || exit 0

base="${HERDR_APP_HOME:-$HOME/.config/herdr-app}/data/research-viewer"
mkdir -p "$base/sessions" "$base/terminals"
f="$base/sessions/$sid.json"
[ -s "$f" ] || echo '{}' > "$f"

new=1
jq -e --arg p "$path" '.notes[]? | select(.path == $p)' "$f" >/dev/null 2>&1 && new=0

tmp="$f.tmp.$$"
jq --arg p "$path" --argjson s "$(date +%s)" \
  '{notes: ([(.notes // [])[] | select(.path != $p)] + [{path: $p, seq: $s}])}' "$f" > "$tmp" \
  && mv "$tmp" "$f" || { rm -f "$tmp"; exit 0; }

# Heal the terminal -> session link in case the SessionStart hook did not run.
[ -n "$term" ] && "$(dirname "$0")/link-session-herdr.sh" "$sid"

if [ -n "$term" ] && { [ "$new" = 1 ] || [ "$flag" = "--reveal" ]; }; then
  focused="$(herdr-app status 2>/dev/null | jq -r '.agent // empty')"
  if [ "$focused" = "$term" ]; then
    herdr-app do layout.show '{"id":"panel"}' >/dev/null 2>&1
    herdr-app do layout.select '{"stack":"panel-body","child":"research"}' >/dev/null 2>&1
  fi
fi
exit 0
