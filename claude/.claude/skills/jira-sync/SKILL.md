---
name: jira-sync
description: Move a Jira ticket to In Progress, In Review or Done and assign it to the user, never backwards, via the Atlassian MCP. Use when an orchestrated task changes state.
---

# Jira sync

Input: a ticket key and a target state (`in_progress`, `in_review`, `done`), usually from the orchestrator's engine output (`{key, to, assign}`). Only for real Jira keys (not `NOJIRA-*`). Tools: Atlassian MCP (`atlassianUserInfo`, `getJiraIssue`, `getTransitionsForJiraIssue`, `transitionJiraIssue`, `editJiraIssue`); load them with ToolSearch if they are deferred.

1. `getJiraIssue` for the ticket: current status and assignee.
2. **Never backwards.** Status order: To Do < In Progress < In Review < Done. If the ticket is already at or past the target, skip the transition (still record it, step 5).
3. **Assign** (when `assign` is true): get the user's account id once with `atlassianUserInfo` and reuse it. If unassigned or already the user's, set `assignee` with `editJiraIssue`. If it is assigned to someone else, do not take it over: tell the user and carry on without assigning.
4. **Transition**: `getTransitionsForJiraIssue`, pick by name, case-insensitive:
   - `in_progress` → "In Progress"
   - `in_review` → "In Review", else "Code Review"
   - `done` → "Done"
   If no transition matches (the workflow may not offer it from the current status), do not guess a different one.
5. **Record the result** so the engine stops asking:
   - success or already there: `python3 ~/.claude/skills/orchestrate2/scripts/orch.py set-jira <KEY> <to>`
   - could not be done: `python3 ~/.claude/skills/orchestrate2/scripts/orch.py set-jira <KEY> failed:<to>` and tell the user in one line why (the available transitions are the useful detail).

A failed transition never stops the run.
