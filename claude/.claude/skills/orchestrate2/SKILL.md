---
name: orchestrate2
description: Run a group of tasks (Jira tickets or any list) as parallel worker + validator sessions, one git worktree each. This only launches the orchestrator agent in its own tab. Only runs when the user types /orchestrate2.
disable-model-invocation: true
---

# Skill: Orchestrate (v2) launcher

This skill is only the entry point. The work is done by three agents (`orchestrator`, `worker`, `validator`, in `~/.claude/agents/`) and the engine script `scripts/orch.py`. Do not do any orchestration in this session: it would fill this conversation's context, and the orchestrator agent runs on its own cheaper, clean session.

Usage: `/orchestrate2 <tickets or task list>`, or `/orchestrate2 continue`.

1. Check you are inside the Herdr app: `echo $HC_TERMINAL_ID` is not empty. If it is empty, say so and stop.
2. From the repo the user is working in (the current directory), run:

   ```bash
   python3 ~/.claude/skills/orchestrate2/scripts/orch.py start "<the user's request, verbatim>"
   ```

   With no request, pass `continue`. It opens the orchestrator in a new tab of this Space, or, if one is already running for this repo, sends it the message instead.
3. Tell the user in one line that the orchestrator is in its own tab and that the graph confirmation and any questions happen there. Then stop.
