#!/usr/bin/env bash
# One-way sync: live Herdr config -> ~/dotfiles/herdr. Never commits or pushes.
# Herdr rewrites its own files, so this package is NOT symlinked with stow.
# Restore the other way with:  rsync -a ~/dotfiles/herdr/.config/ ~/.config/
#
# What counts as "mine": Herdr lists everything it ships in herdr-app/installed.json.
# Any mod folder, layout or theme NOT shipped by Herdr is treated as mine and synced,
# so new mods and layouts are picked up automatically.
set -euo pipefail

REPO="${DOTFILES_DIR:-$HOME/dotfiles}"
SRC="${HERDR_SRC:-$HOME/.config}"
DEST="$REPO/herdr/.config"
APP="$SRC/herdr-app"
INSTALLED="$APP/installed.json"
BIG_MB=5   # warn when a single mod is bigger than this (probably bundled assets)

command -v jq >/dev/null || { echo "jq is required (brew install jq)" >&2; exit 1; }
# Without the shipped list we can't tell mine from Herdr's, so refuse rather than copy everything.
[ -s "$INSTALLED" ] || { echo "Missing $INSTALLED: can't tell my mods from Herdr's. Aborting." >&2; exit 1; }

shipped() { jq -e --arg k "$1" 'has($k)' "$INSTALLED" >/dev/null; }

copy_file() {   # copy_file <path relative to $SRC>
  mkdir -p "$DEST/$(dirname "$1")"
  cp -p "$SRC/$1" "$DEST/$1"
}

echo "== syncing =="
# Fixed files
for f in herdr/config.toml herdr-app/settings.json herdr-app/keys.json; do
  if [ -f "$SRC/$f" ]; then copy_file "$f"; echo "file    $f"; fi
done

# My mods: folders not shipped by Herdr
my_mods=()
for d in "$APP"/mods/*/; do
  m=$(basename "$d")
  shipped "$m" && continue
  my_mods+=("$m")
  mkdir -p "$DEST/herdr-app/mods/$m"
  rsync -a --delete --exclude gpui-kit.d.ts --exclude jsconfig.json --exclude '*.error' \
    "$d" "$DEST/herdr-app/mods/$m/"
  size_mb=$(( $(du -sk "$DEST/herdr-app/mods/$m" | cut -f1) / 1024 ))
  echo "mod     $m"
  if [ "$size_mb" -ge "$BIG_MB" ]; then echo "  WARNING: $m is ${size_mb}MB. Bundled third-party assets? Check before pushing."; fi
done

# My layouts: layouts/<name>.json not shipped as "layout:<name>"
my_layouts=()
for f in "$APP"/layouts/*.json; do
  n=$(basename "$f" .json)
  shipped "layout:$n" && continue
  my_layouts+=("$n")
  copy_file "herdr-app/layouts/$n.json" && echo "layout  $n"
done

# My themes: themes/<name>.json, except ones that belong to a shipped layout (e.g. win98)
my_themes=()
for f in "$APP"/themes/*.json; do
  [ -e "$f" ] || continue
  n=$(basename "$f" .json)
  shipped "layout:$n" && continue
  my_themes+=("$n")
  copy_file "herdr-app/themes/$n.json" && echo "theme   $n"
done

# Anything in the repo that no longer exists live: report, never delete automatically
echo "== stale in repo (deleted or shipped live; remove with git rm if intended) =="
stale=0
check_stale() {  # check_stale <label> <dir in repo> <suffix> <live names...>
  local label=$1 dir=$2 suffix=$3; shift 3
  [ -d "$dir" ] || return 0
  for p in "$dir"/*"$suffix"; do
    [ -e "$p" ] || continue
    n=$(basename "$p" "$suffix"); keep=0
    for l in "$@"; do
      if [ "$l" = "$n" ]; then keep=1; fi
    done
    if [ $keep -eq 0 ]; then echo "  $label $n"; stale=1; fi
  done
  return 0
}
check_stale mod    "$DEST/herdr-app/mods"    ""      ${my_mods[@]+"${my_mods[@]}"}
check_stale layout "$DEST/herdr-app/layouts" ".json" ${my_layouts[@]+"${my_layouts[@]}"}
check_stale theme  "$DEST/herdr-app/themes"  ".json" ${my_themes[@]+"${my_themes[@]}"}
if [ $stale -eq 0 ]; then echo "  none"; fi

cd "$REPO"
echo "== gitleaks =="
if command -v gitleaks >/dev/null; then
  gitleaks detect --no-git --source herdr --redact 2>&1 | grep -E 'leaks found|leaks:' || true
else
  echo "gitleaks not installed, skipped"
fi
echo "== changes (not committed) =="
git status --short herdr
git diff --stat herdr
echo "Review with: cd ~/dotfiles && git diff herdr   then commit and push yourself."
