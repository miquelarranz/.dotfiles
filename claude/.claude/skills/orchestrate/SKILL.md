---
name: orchestrate
description: Orchestrate a group of tasks (Jira tickets or any list) across tabs of the Herdr desktop app. Each task runs in its own `claude --worktree` session in a new tab of the current Space (started from the main repo folder), ordered by dependencies and stacked PRs. Only runs when the user types /orchestrate.
disable-model-invocation: true
---

# Skill: Orchestrate

You are the **orchestrator**: a thin scheduler. You never implement tasks yourself. You build a dependency graph, launch one worker Claude session per task in its own worktree and its own tab, react as workers finish or block, and release dependents. Workers stop at a **draft PR**. You never merge anything.

Usage: `/orchestrate <tickets or task list>`. Input is Jira keys/URLs/JQL, or free-form items.

## Which Herdr

The desktop app (herdr-core) is driven with the **`hc`** CLI. Do **not** use the `herdr` CLI (`herdr tab`, `herdr agent`...): it talks to a different, invisible server, and tabs created there never show in the app.

- `hc run --bg` starts a worker as a live terminal; `hc send` types into it; `hc wait` blocks until it stops working; `hc read` prints its screen.
- A terminal is not a tab by itself. To show it, add a tab to the app layout with `scripts/hc_tab.py` (next to this file). It edits the app's layout doc with a version check and puts the tab in the Space that contains your own terminal (`$HC_TERMINAL_ID`).

## 0. Preconditions

- `$HC_TERMINAL_ID` must be set and `hc ls` must work (you are inside the Herdr app). Otherwise stop and say so.
- Identify the repo from the cwd (`git rev-parse --show-toplevel`). If tasks clearly span several repos, say so and ask which repo each belongs to.
- **Prepare the main checkout.** Work from the existing repo folder, never a separate worktrees folder (anything outside the repo triggers permission prompts for the workers). Require a clean tree (`git status --porcelain` empty), else stop and ask. Then `git switch <base>` and `git pull --ff-only` so the base branch is current. Default base: the repo's default branch.
- Read the `engineering:github-workflow` skill for branch, commit and PR conventions. Every task needs a Jira key (Typeform repos); for free-form items without one, ask the user once, up front, for each key or an explicit `NOJIRA-123`.
- Generate each task's branch name as `<prefix>/<KEY>_<snake_case_description>` (prefix `feat|fix|chore`, description 1-10 words), e.g. `feat/TU-456_add_audience_export`. Show the branch names in the graph table.

## 1. Build the task graph

1. Fetch each ticket (Atlassian MCP `getJiraIssue`; load via ToolSearch if deferred). Take: key, summary, description, acceptance criteria, `issuelinks` ("blocks" / "is blocked by"), parent/epic. For free-form items, use the text as given.
2. Dependencies: start from Jira links, then infer extra edges from ticket text (shared files, "after X", API/contract changes). Mark each edge `jira` or `inferred`.
3. Detect cycles. Group tasks into waves (wave 1 = no unmet dependencies).
4. **Present the graph and wait for confirmation** before launching anything: a table of `key | title | branch | depends on | edge source | wave`, plus any assumptions. The user may edit edges. Also state: concurrency cap (default **4** running workers), worker permission mode (default **`auto`**, so workers run autonomously with no prompts), and the base branch.

A task with **several** parents cannot stack on two branches. Ask the user: stack on the main parent and merge the other parent branches into the child worktree, or wait for the others to merge.

## 2. State

Keep state in `<repo>/.git/orchestrate/<run-id>/state.json` (`<run-id>` = `YYYYMMDD-HHMM`; create it). It sits inside the repo, so no permission prompts, and git never tracks it. Workers do not touch it. Per task: `{key, branch, worktree, terminal, name, parent, status, pr_url}`. Statuses: `pending`, `running`, `blocked`, `done` (draft PR open), `merged`, `failed`.

The handoff from worker to orchestrator and to dependent tasks is the **draft PR body** (read it with `gh pr view`), not files.

Update `state.json` after every transition. If the user re-invokes `/orchestrate continue` (or the session is lost), reload the latest `state.json`, reconcile it with `hc ls` and `gh pr view`, and carry on.

## 3. Launch a task

For a task whose parents are all `done` (stacked) or `merged`, and while running workers < cap. `<name>` is the lowercase key (`hc` names: a-z 0-9 - _, max 32).

```bash
# 1. Never duplicate: if `hc ls` already has a live terminal named <name>, reuse it, do not start another.

# 2. Start Claude in the MAIN repo folder with its own worktree
#    (claude creates <repo>/.claude/worktrees/<KEY> itself).
#    --outlive-parent: workers survive if this orchestrator session ends.
#    --no-notify: do not type notes into this session; you track completion yourself.
hc run --bg --name <name> --cwd <repo> --outlive-parent --no-notify \
  claude --worktree <KEY> --permission-mode <mode> --add-dir <repo> \
  --allowedTools "Bash(git *),Bash(gh *),mcp__github__create_pull_request"
#   <mode> defaults to auto. --add-dir keeps the main repo and its worktree readable/writable.
#   -> JSON with "address" (e.g. t35)

# 3. Show it as a tab in this Space
python3 <this skill dir>/scripts/hc_tab.py add <address> <KEY>

# 4. Wait for Claude to be ready, then hand over the task
hc wait <name> --timeout 60
hc send <name> "<worker prompt>"
```

If any step after 2 fails, clean up: `python3 .../hc_tab.py remove <address>` and `hc kill <name>`; mark the task `failed` and tell the user.

`claude --worktree` branches from the default branch, so for a stacked task the worker's first step is to move onto the parent branch (it is in the worker prompt). Record the worktree path as `<repo>/.claude/worktrees/<KEY>`.

Then start a **background** watcher (Bash `run_in_background`) so you are notified when the worker settles:

```bash
hc wait <name> --timeout 3600
```

### Worker prompt (fill in, keep it self-contained)

> You are working on **<KEY>: <summary>** in this git worktree, created for this task by `claude --worktree`. Do not touch other worktrees or the main checkout.
>
> Ticket:
> <description + acceptance criteria>
>
> First, set up the branch (the worktree is fresh, so nothing is lost):
> - If **not stacked**: `git fetch origin`, `git reset --hard origin/<base>`.
> - If **stacked** on `<parent KEY>` (branch `<parent-branch>`): `git fetch origin`, `git reset --hard origin/<parent-branch>`. Read the parent's draft PR (`gh pr view <parent-pr>`) for context.
> - Then rename the branch: `git branch -m <branch>`.
>
> Then: plan briefly, implement test-first, run the repo's tests and linter, commit as `prefix(<KEY>): lowercase description` (conventional commits), push the branch, and open a **draft PR** using the **`create-pr` skill**. The PR base is `<parent-branch-or-base>` (not main when stacked). The title must be `prefix(<KEY>): short summary` and the description must carry the Jira link for `<KEY>`. Do not merge, and do not mark the PR ready.
>
> You are fully autonomous: the human will not answer questions, approve commands or confirm steps until the PR exists. Do not ask for the Jira ticket (it is `<KEY>`) or for confirmation of the plan; decide, state assumptions in the PR body, and keep going. Only stop if the task cannot be done at all without information you do not have.
>
> Put in the PR body anything a dependent task needs to know (interfaces, decisions, open questions). Finish with a short message: PR URL and one-line summary. Then stop.
>
> If you are blocked on a decision only a human can make, say so plainly and wait.

## 4. React to workers

When a watcher exits (background-task notification), `hc wait` only says the worker stopped working. Work out why:
- `hc read <name>` for the screen. A permission prompt or question means **blocked** (rare in auto mode; treat it as a real problem and tell the user).
- Otherwise find the PR for the task's branch (`gh pr list --head <branch>`).
- **done**: a draft PR exists with the expected base. Mark `done`, run the cleanup in 4b, then launch any dependents that are now unblocked, up to the cap. If the worker stopped with no PR and no question, mark `failed` and tell the user what its last output says.
- **blocked**: mark `blocked`, summarise what it is waiting for, and ask the user. Relay answers with `hc send <name> "<text>"`; for a permission prompt that needs a keypress use `hc keys <name> enter` (or the right keys). Restart the watcher after unblocking. If the worker stalled with no output change for a long time, tell the user; do not kill it.

### 4b. Clean up a finished task (automatic)

Once a task is `done`, free its resources without asking, but only if all checks pass:
1. The worktree is clean: `git -C <worktree> status --porcelain` is empty.
2. Nothing is unpushed: `git -C <worktree> rev-list origin/<branch>..HEAD --count` is `0` (fetch first).
3. The draft PR exists (already verified above).

If any check fails, skip the cleanup, keep everything, and tell the user why. Never use `--force`.

If all pass, in this order (Claude holds the worktree open, so end it first):
```bash
hc read <name> | tail -n 15            # keep the worker's final summary for the report
hc kill <name>; hc rm <name>           # end the session
python3 <this skill dir>/scripts/hc_tab.py remove <address>   # close the tab
git -C <repo> worktree remove <repo>/.claude/worktrees/<KEY>  # no --force
```
Keep the **branch** (the PR needs it). Mark the task `cleaned` in `state.json`. The worker's conversation stays resumable with `claude --resume`.

Dependents are not affected: a stacked child starts from `origin/<parent-branch>`, not from the parent's worktree.

After every event print a compact status table (`key | status | parent | PR | terminal`).

## 5. Merges and retargeting

You never merge. When the user says a parent PR was merged (or you see it with `gh pr view <n> --json state`):
1. Mark the parent `merged`.
2. For each stacked child: `gh pr edit <child-pr> --base <base>`. The child's worktree was probably cleaned up, so use a temporary one: `git -C <repo> worktree add <repo>/.claude/worktrees/<KEY>-rebase <child-branch>`, then `git -C <that> rebase --onto origin/<base> <old-parent-branch>`, `git push --force-with-lease`, and remove the temporary worktree. If the rebase conflicts, do not resolve silently: tell the user and keep the temporary worktree for them.
3. If a parent PR gets new commits after review (child is behind it), tell the user and offer to rebase the child onto the updated parent.

## 5b. Optional watching (only when useful)

Worker completion is event-driven (the background `hc wait` watchers). Do not poll for it.

After the first wave is launched, if any **stacked edge** is pending (a parent PR that must merge before its child can be retargeted), offer once: "Watch for parent merges every ~15 minutes?" Only on a yes, schedule a self-paced check (ScheduleWakeup, 900s, passing the same `/orchestrate continue` prompt). Each tick:
1. For each parent with stacked children, run `gh pr view <n> --json state`. If merged, do step 5 above.
2. Flag any `running` worker whose screen (`hc read <name>`) is unchanged since the previous tick, as possibly hung. Tell the user; do not kill it.
3. If nothing changed, reschedule with `noop: true`. Stop the loop (`stop: true`) once no stacked edges are pending and no worker is `running`.

Skip this for flat lists with no stacked edges.

## 6. Finish

When every task is `done` or `merged`, print the final table with PR links and the dependency order for reviewing and merging. Finished tasks are already cleaned (4b). List any task that was not (failed, blocked, or a cleanup check failed) with what is left over, and offer to remove it. Offer to delete the local branches of merged tasks; never delete a branch unasked, and never remove a worktree with uncommitted changes.

## Rules

- Use `hc`, never the `herdr` CLI.
- Never implement a task yourself, and never merge or mark a PR ready.
- Never force-push or delete branches without asking.
- Never launch before the user confirms the graph.
- Stay within the concurrency cap; queue the rest.
- Never write the app layout doc except through `hc_tab.py`, which version-checks the write.
