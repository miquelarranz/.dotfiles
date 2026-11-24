---
name: jira-sync
description: Move a Jira ticket to In Progress, In Review or Done and assign it to the user, never backwards, via the Atlassian MCP. Use when an orchestrated task changes state.
---

# Jira sync

Input: a ticket key and a target state (`in_progress`, `in_review`, `done`), usually from the orchestrator's engine output (`{key, to, assign}`). Only for real Jira keys (not `NOJIRA-*`). Tools: the claude.ai Atlassian connector (`mcp__claude_ai_Atlassian_MCP__*`: `atlassianUserInfo`, `getJiraIssue`, `editJiraIssue`, `transitionJiraIssue`, and `executeRead` for listing transitions); load them with ToolSearch if they are deferred. `cloudId` is the site hostname, `typeform.atlassian.net`, or the value from `getAccessibleAtlassianResources`.

1. `getJiraIssue` (`issueIdOrKey`, `cloudId`) for the ticket: current status and assignee.
2. **Never backwards.** Status order: To Do < In Progress < In Review < Done. If the ticket is already at or past the target, skip the transition (still record it, step 5).
3. **Assign** (when `assign` is true): get the user's account id once with `atlassianUserInfo` and reuse it. If unassigned or already the user's, set `assignee` with `editJiraIssue` (`fields: {"assignee": "<accountId>"}`). If it is assigned to someone else, do not take it over: tell the user and carry on without assigning.
4. **Transition**: list what the ticket can do now with `executeRead({ name: "listJiraIssueTransitions", cloudId, inputs: { issueIdOrKey } })`. Each transition has its own name and the status it leads to; **match the status it leads to**, case-insensitive, not the transition's name:
   - `in_progress` → "In Progress"
   - `in_review` → "In Review", else "Code Review"
   - `done` → "Done"
   Then `transitionJiraIssue({ cloudId, issueIdOrKey, transitionId })`. If no transition leads there (the workflow may not offer it from the current status), do not guess a different one.
5. **Record the result** so the engine stops asking:
   - success or already there: `python3 ~/.claude/skills/orchestrate/scripts/orch.py set-jira <KEY> <to>`
   - could not be done: `python3 ~/.claude/skills/orchestrate/scripts/orch.py set-jira <KEY> failed:<to>` and tell the user in one line why (the available transitions are the useful detail).

A failed transition never stops the run.
