#!/usr/bin/env bash
# SessionStart hook: link this Claude session to its Herdr terminal, and on resume reveal the
# Research panel if the session already has notes (only when its terminal is the focused one).
# Prints nothing (SessionStart stdout would be injected into the conversation).
set -u
command -v jq >/dev/null || exit 0
input="$(cat)"
sid="$(printf '%s' "$input" | jq -r '.session_id // empty')"
source_="$(printf '%s' "$input" | jq -r '.source // empty')"
"$(dirname "$0")/link-session-herdr.sh" "$sid" >/dev/null 2>&1

if [ "$source_" = "resume" ] && [ -n "${HC_TERMINAL_ID:-}" ]; then
  f="${HERDR_APP_HOME:-$HOME/.config/herdr-app}/data/research-viewer/sessions/$sid.json"
  if [ -s "$f" ] && [ "$(jq '.notes | length' "$f" 2>/dev/null || echo 0)" -gt 0 ]; then
    # Detached: Herdr needs a moment to register the agent as focused; never block startup.
    ( sleep 2
      if [ "$(herdr-app status 2>/dev/null | jq -r '.agent // empty')" = "$HC_TERMINAL_ID" ]; then
        herdr-app do layout.show '{"id":"panel"}' >/dev/null 2>&1
        herdr-app do layout.select '{"stack":"panel-body","child":"research"}' >/dev/null 2>&1
      fi ) >/dev/null 2>&1 &
    disown 2>/dev/null
  fi
fi
exit 0
