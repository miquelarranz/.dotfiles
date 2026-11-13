#!/usr/bin/env bash
# Record that Claude session <id> now runs in this Herdr terminal (HC_TERMINAL_ID).
# The terminal keeps its last 10 sessions, newest first, so notes survive /clear and --resume.
#   usage: link-session-herdr.sh <session id>
set -u
sid="${1:-}"; term="${HC_TERMINAL_ID:-}"
[ -n "$sid" ] && [ -n "$term" ] && command -v jq >/dev/null || exit 0
dir="${HERDR_APP_HOME:-$HOME/.config/herdr-app}/data/research-viewer/terminals"
mkdir -p "$dir"
f="$dir/$term.json"; [ -s "$f" ] || echo '{}' > "$f"
tmp="$f.tmp.$$"
jq --arg s "$sid" '{sessions: ([$s] + [(.sessions // [])[] | select(. != $s)] | .[0:10])}' "$f" > "$tmp" \
  && { cmp -s "$tmp" "$f" && rm -f "$tmp" || mv "$tmp" "$f"; } || rm -f "$tmp"
exit 0
