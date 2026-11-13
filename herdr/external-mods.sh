#!/usr/bin/env bash
# Install or update Herdr mods other people wrote, listed in external-mods.txt.
# They are never copied into this repo: only the url and a pinned commit are tracked here.
#   external-mods.sh install   (default) fetch each mod at its pinned commit into ~/.config/herdr-app/mods
#   external-mods.sh update    move each pin to the remote's HEAD, then install
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
LIST="$HERE/external-mods.txt"
MODS="${HERDR_APP_HOME:-$HOME/.config/herdr-app}/mods"
cmd="${1:-install}"
[ -f "$LIST" ] || { echo "No $LIST" >&2; exit 1; }

entries() { grep -vE '^[[:space:]]*(#|$)' "$LIST"; }

install_one() {  # install_one <name> <url> <sha> <subpath>
  local name=$1 url=$2 sha=$3 sub=$4 tmp
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' RETURN
  git -C "$tmp" init -q
  git -C "$tmp" remote add origin "$url"
  git -C "$tmp" fetch -q --depth 1 origin "$sha"
  git -C "$tmp" checkout -q FETCH_HEAD
  [ -f "$tmp/$sub/main.js" ] || { echo "$name: $sub/main.js not found in $url@${sha:0:7}" >&2; return 1; }
  mkdir -p "$MODS/$name"
  # Herdr writes gpui-kit.d.ts, jsconfig.json and *.error into mod folders; the mod caches avatars. Keep them.
  rsync -a --delete --exclude gpui-kit.d.ts --exclude jsconfig.json --exclude '*.error' --exclude .avatars \
    "$tmp/$sub/" "$MODS/$name/"
  echo "installed $name @ ${sha:0:7} ($url)"
  if command -v herdr-app >/dev/null 2>&1; then herdr-app check "$MODS/$name" 2>&1 | tail -1 || true; fi
}

case "$cmd" in
  install)
    entries | while read -r name url sha sub; do install_one "$name" "$url" "$sha" "$sub"; done ;;
  update)
    out="$(mktemp)"
    while IFS= read -r line; do
      if [[ "$line" =~ ^[[:space:]]*(#|$) ]]; then echo "$line" >> "$out"; continue; fi
      read -r name url sha sub <<<"$line"
      new="$(git ls-remote "$url" HEAD | cut -f1)"
      [ -n "$new" ] || { echo "$name: cannot reach $url" >&2; cp "$LIST" "$out"; break; }
      [ "$new" = "$sha" ] && echo "$name already at ${sha:0:7}" || echo "$name: ${sha:0:7} -> ${new:0:7}"
      echo "$name  $url  $new  $sub" >> "$out"
    done < "$LIST"
    mv "$out" "$LIST"
    "$0" install ;;
  *) echo "usage: external-mods.sh [install|update]" >&2; exit 2 ;;
esac
