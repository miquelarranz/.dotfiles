@RTK.md

When I ask you to investigate, research, dig into, or look into something, use the `investigate` skill — the findings belong in my Obsidian vault, not only in the terminal.

Once a vault note exists for the topic we are on, that file is the deliverable, not the chat. Every later refinement, correction, scope cut or reversal must be written back into it in the same turn that I ask for it — before you print the terminal summary. Never leave a design change living only in the conversation. If plan mode is blocking the write, say so plainly and write it the moment plan mode ends. I should never have to ask you to update the note.

When I say "sync dotfiles" or "sync herdr", run `~/dotfiles/sync-herdr.sh` (live Herdr config → repo, one way), show me the `git diff`, and stop. Herdr's config is deliberately not symlinked because the app rewrites it. Never commit or push `~/dotfiles` unless I explicitly say so. The other packages (zsh, git, claude, ccstatusline) are stow symlinks and need no sync; if I add a new file to one of them, tell me it still has to be committed.
