---
name: rebase-and-push
description: Safely rebase a task branch onto its base (or onto main after its parent PR merged) and push it with --force-with-lease. Use when a PR branch is behind its base or its parent merged.
---

# Rebase and push

Only for **your own task branch** (`feat|fix|chore/<KEY>_...`), never `main`, `master`, `develop` or a branch someone else owns. The lease form is the only force-push allowed; plain `--force` / `-f` are denied.

1. Everything committed: `git status --porcelain` must be empty. Never rebase with local changes.
2. `git fetch origin`.
3. Rebase:
   - Normal (base moved): `git rebase origin/<base>`.
   - Stacked child whose parent PR merged (the base is now `main`): `git rebase --onto origin/<base> <fork-point>`, where `<fork-point>` is the parent branch tip the child started from (the orchestrator gives it to you; if not, `git merge-base origin/<parent-branch> HEAD` while the parent branch still exists).
4. **Conflicts**:
   - Worker: resolve the files, `git add` them, `GIT_EDITOR=true git rebase --continue`. Re-run the project checks (`run-checks`) afterwards.
   - Validator: you cannot edit files. `git rebase --abort`, then report the conflicting files to the worker as findings.
5. Push with a lease pinned to what you fetched, so you never overwrite commits you have not seen:
   `git push --force-with-lease=<branch>:$(git rev-parse origin/<branch>) origin HEAD:<branch>`
   If it is rejected, someone else pushed: `git fetch`, look at `git log HEAD..origin/<branch>`, and stop and report rather than retrying with a plain force.
6. After a rebase of a PR whose base changed, the orchestrator retargets the PR; you do not need to run `gh pr edit` unless asked.
