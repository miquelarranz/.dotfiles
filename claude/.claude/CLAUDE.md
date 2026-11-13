@RTK.md

The `investigate` skill runs only when I type `/investigate <topic>`. Do not start vault notes on your own. Once it has created a note in this conversation, that note is the deliverable: write every later change back into it in the same turn, before the terminal summary. The skill has the details.

When I say "sync dotfiles" or "sync herdr", run `~/dotfiles/sync-herdr.sh` (live Herdr config → repo, one way), show me the `git diff`, and stop. Herdr's config is deliberately not symlinked because the app rewrites it. Never commit or push `~/dotfiles` unless I explicitly say so. The other packages (zsh, git, claude, ccstatusline) are stow symlinks and need no sync; if I add a new file to one of them, tell me it still has to be committed.
