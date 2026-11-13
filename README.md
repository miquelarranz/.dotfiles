# dotfiles

Personal config, managed with [GNU Stow](https://www.gnu.org/software/stow/). One folder per tool, mirroring `$HOME`.

```sh
brew install stow
git clone git@github.com:miquelarranz/.dotfiles.git ~/dotfiles
cd ~/dotfiles
stow --no-folding claude git zsh ccstatusline herdr
```

`--no-folding` links individual files, so tools that write into `~/.claude` or `~/.config/herdr*` don't pollute this repo.

## Packages
- `claude`: `CLAUDE.md`, `RTK.md`, status line script
- `git`: `.gitconfig` (identity comes from an untracked `~/.gitconfig.local`), global ignore
- `zsh`: `.zshrc`, `.zprofile` (machine-specific and secret settings go in an untracked `~/.zshrc.work`)
- `ccstatusline`: settings
- `herdr`: config, theme, keys, layout, my mods

Not tracked: secrets, tokens, history, caches, generated files.
