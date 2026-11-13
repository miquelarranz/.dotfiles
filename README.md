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
| `herdr` | **copied, not linked** | config, theme, keys, layout, my mods (`my-tabs`, `spaces-edited`, `newtab-root`, `research-viewer`). Other people's mods are pinned, not copied (see below) |

Stow runs with `--no-folding` so tools that write into `~/.claude` don't pollute this repo.

Herdr rewrites its own config files, so it is not symlinked. Save live changes into the repo with `./sync-herdr.sh`, review the diff, then commit and push. The script treats any mod, layout or theme that Herdr doesn't ship (it reads `herdr-app/installed.json`) as mine, so new ones are picked up automatically. Entries deleted live are only reported, never removed from the repo. Restore with `rsync -a herdr/.config/ ~/.config/`.

## Claude Code settings

`docs/claude-settings.personal.json` is a snapshot of my `settings.json` with everything employer-specific removed (plugins, marketplaces, work MCP permissions, PR hooks, auto-mode environment). It is not symlinked, because `settings.json` mixes personal and work settings on my main machine. `bootstrap.sh` copies it to `~/.claude/settings.json` only if none exists. It can drift, so regenerate it after changing settings.

## Mods other people wrote

`herdr/external-mods.txt` lists them as `name  url  commit  path-in-repo`. They are not copied into this repo: `bootstrap.sh` runs `herdr/external-mods.sh install`, which fetches each one at its pinned commit into `~/.config/herdr-app/mods/`. `./herdr/external-mods.sh update` moves every pin to the remote's latest commit and installs it, so bumping a mod is a one-line diff to review. `sync-herdr.sh` skips anything listed there. Currently: `github-prs` from [jeffarese/herdr-prs](https://github.com/jeffarese/herdr-prs) (the Pull requests tab in the right panel).

## Research panel

The right panel has a "Research notes" tab (mod `research-viewer`, layout block `research`) that renders the vault notes written by the focused Claude session. It only appears for sessions that have notes. Notes are registered by Claude Code hooks in `claude/.claude/hooks/` (`PostToolUse` on Write/Edit under `~/Desktop/Vault/Research/`, plus `SessionStart`), keyed by Claude session id, in `~/.config/herdr-app/data/research-viewer/`. Those hooks are wired in `~/.claude/settings.json`, which is not tracked here, so add them by hand on a new machine. `backfill-research-sessions.py` registers notes from past session transcripts. The tab strip (`my-tabs`) carries its own copy of `registry.js` as `research-registry.js`, because mods cannot import across folders: keep them identical.

## Not tracked

Secrets, tokens, history, caches and generated files, plus anything employer-specific.
