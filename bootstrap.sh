#!/usr/bin/env bash
# Set up a new machine from this repo. Safe to re-run; never overwrites existing files.
#   git clone git@github.com:miquelarranz/.dotfiles.git ~/dotfiles && ~/dotfiles/bootstrap.sh
set -euo pipefail

REPO="${DOTFILES_DIR:-$HOME/dotfiles}"
cd "$REPO"

# 1. Tools
if command -v brew >/dev/null 2>&1; then
  brew bundle --file "$REPO/Brewfile"
else
  echo "Homebrew not found. Install it from https://brew.sh, then re-run." >&2
  exit 1
fi

# 2. Symlinked packages. Dry-run first: if any real file is in the way, stop and say which.
PKGS=(claude git zsh ccstatusline)
if ! out=$(stow -n --no-folding "${PKGS[@]}" 2>&1); then
  echo "Existing files are in the way. Move or back them up, then re-run:" >&2
  echo "$out" | grep -E 'existing target|cannot stow' >&2 || echo "$out" >&2
  exit 1
fi
stow --no-folding "${PKGS[@]}"
echo "Linked: ${PKGS[*]}"

# 3. Herdr is copied, not linked (the app rewrites its own config). Existing files are kept.
mkdir -p "$HOME/.config"
rsync -a --ignore-existing "$REPO/herdr/.config/" "$HOME/.config/"
echo "Copied herdr config (existing files kept)"

# 4. Untracked, per-machine files that .zshrc and .gitconfig expect
[ -e "$HOME/.zshrc.work" ] || { printf '# Machine- or employer-specific shell config and secrets. Not in the repo.\n' > "$HOME/.zshrc.work"; chmod 600 "$HOME/.zshrc.work"; }
if [ ! -e "$HOME/.gitconfig.local" ]; then
  printf '[user]\n\tname = YOUR NAME\n\temail = you@example.com\n' > "$HOME/.gitconfig.local"
  chmod 600 "$HOME/.gitconfig.local"
  echo "NOTE: edit ~/.gitconfig.local and set your git name and email."
fi

echo "Done. Open a new shell. Select the Herdr layout once with:"
echo "  herdr-app do layout.switch '{\"name\":\"my-layout\"}'"
