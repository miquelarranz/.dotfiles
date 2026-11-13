# dotfiles

Personal config, managed with [GNU Stow](https://www.gnu.org/software/stow/). One folder per tool, mirroring `$HOME`.

## New machine

```sh
git clone git@github.com:miquelarranz/.dotfiles.git ~/dotfiles
~/dotfiles/bootstrap.sh
```

`bootstrap.sh` installs the tools in `Brewfile`, links the packages, copies the Herdr config and creates empty `~/.zshrc.work` and `~/.gitconfig.local`. It never overwrites existing files: if something is in the way it stops and lists it.

## Packages

| Package | How it is installed | Contents |
|---|---|---|
| `claude` | stow symlinks | `CLAUDE.md`, `RTK.md`, status line script, `investigate` skill |
| `git` | stow symlinks | `.gitconfig` (identity comes from an untracked `~/.gitconfig.local`), global ignore |
| `zsh` | stow symlinks | `.zshrc`, `.zprofile` (machine-specific and secret settings go in an untracked `~/.zshrc.work`) |
| `ccstatusline` | stow symlinks | settings |
| `herdr` | **copied, not linked** | config, theme, keys, layout, my mods (`prs`, `my-tabs`, `spaces-edited`, `newtab-root`) |

Stow runs with `--no-folding` so tools that write into `~/.claude` don't pollute this repo.

Herdr rewrites its own config files, so it is not symlinked. Save live changes into the repo with `./sync-herdr.sh`, review the diff, then commit and push. Restore with `rsync -a herdr/.config/ ~/.config/`.

## Claude Code settings

`docs/claude-settings.personal.json` is a snapshot of my `settings.json` with everything employer-specific removed (plugins, marketplaces, work MCP permissions, PR hooks, auto-mode environment). It is not symlinked, because `settings.json` mixes personal and work settings on my main machine. `bootstrap.sh` copies it to `~/.claude/settings.json` only if none exists. It can drift, so regenerate it after changing settings.

## Herdr PR panel

The `prs` mod gives a popup (`mod.open:prs`, bound to `cmd-alt-p`). The sidebar tab only exists in `my-layout`, so to get it, use the layout too: `herdr-app do layout.switch '{"name":"my-layout"}'`.

## Not tracked

Secrets, tokens, history, caches and generated files, plus anything employer-specific.
