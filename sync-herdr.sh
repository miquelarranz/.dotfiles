#!/usr/bin/env bash
# One-way sync: live Herdr config -> ~/dotfiles/herdr. Never commits or pushes.
# Herdr rewrites its own files, so this package is NOT symlinked with stow.
# Restore the other way with:  rsync -a ~/dotfiles/herdr/.config/ ~/.config/
set -euo pipefail

REPO="$HOME/dotfiles"
SRC="$HOME/.config"
DEST="$REPO/herdr/.config"

files=(
  herdr/config.toml
  herdr-app/settings.json
  herdr-app/keys.json
  herdr-app/layouts/my-layout.json
  herdr-app/themes/cathode-amber.json
)
mods=(my-tabs spaces-edited newtab-root prs)   # my own mods only; bundled ones are not tracked

for f in "${files[@]}"; do
  mkdir -p "$DEST/$(dirname "$f")"
  cp -p "$SRC/$f" "$DEST/$f"
done

for m in "${mods[@]}"; do
  mkdir -p "$DEST/herdr-app/mods/$m"
  rsync -a --delete --exclude gpui-kit.d.ts --exclude jsconfig.json \
    "$SRC/herdr-app/mods/$m/" "$DEST/herdr-app/mods/$m/"
done

cd "$REPO"
echo "== gitleaks =="
gitleaks detect --no-git --source herdr --redact 2>&1 | grep -E 'leaks found|leaks' || true
echo "== changes (not committed) =="
git status --short herdr
git diff --stat herdr
echo "Review with: cd ~/dotfiles && git diff herdr   then commit and push yourself."
