---
name: investigate
description: Run an investigation and store the findings as a categorised Markdown note in the Obsidian vault (~/Desktop/Vault/Research/<Area>/). Only runs when the user types /investigate <topic>.
disable-model-invocation: true
---

# Skill: Investigate

Produce a durable research note in the vault, plus a short terminal summary. The note is plain Markdown with YAML frontmatter, so it must stay readable outside Obsidian (any editor or previewer).

The topic is whatever follows `/investigate`.

## Destination

- Vault: `~/Desktop/Vault`
- Folder: `Research/<Area>/`, exactly one level of area folders. Never nest deeper.
- Filename: `YYYY-MM-DD <Title Case Topic>.md`, e.g. `2026-09-08 Consent Management Extensibility.md`
- Date is today's date from the session context.
- Helper files (scripts, JSON dumps, CSVs) go in `Research/<Area>/_files/`, never next to the notes. Name them `YYYY-MM-DD <Topic> - <what>.<ext>` and link them from the note.

## Steps

### 1. Scope and area

Restate the question in one line, then pick the area:

1. `ls -d ~/Desktop/Vault/Research/*/` to list the existing areas.
2. Pick the one that fits. Do not create near-duplicates (`Testing` next to `Test Tooling`).
3. If nothing fits, propose a new Title Case area name.

If the question has two materially different readings, or a new area is needed, ask once, up front, in a single message. Otherwise proceed.

### 2. Check for prior work

Search all areas, not just the chosen one: `ls -R ~/Desktop/Vault/Research/` and `rg -il "<keywords>" ~/Desktop/Vault/Research`.

- **Same topic, same day** → update that note in place.
- **Same topic, earlier date** → new note, and `[[wikilink]]` both ways. Set the old note's `status: superseded`.
- **Adjacent topic, any area** → `[[wikilink]]` it from Related.

### 3. Investigate

Delegate fact-gathering to background agents with fresh contexts, so you keep working. Up to 3 in one message over independent areas (`Explore` for code location, `general-purpose` for open questions).

- Use primary sources: source code, official docs, specs, first-party APIs, the ticket or Notion page itself. Follow every claim back to the source that owns it, not a secondary write-up.
- Pull from whatever is connected (Notion, Slack, Jira, Datadog, GitHub, web) instead of reasoning from memory.

### 4. Write the note

```markdown
---
date: YYYY-MM-DD
updated: YYYY-MM-DD
area: <Area folder name>
type: <design | investigation | inventory | incident | decision | reference>
status: <draft | stable | superseded>
tags: [<up to 5, lowercase-kebab>]
sources: [<repo names, doc titles, ticket keys>]
---

## Question

One line: what was asked.

## Answer

The conclusion first, in 3–6 lines. Someone reading only this section should get the decision.

## Findings

Substance, organised by theme. Every claim about code carries a `path/file.go:123` citation. Every claim about a document names it.

## Open questions

What is still unresolved, and who or what would resolve it.

## Related

`[[wikilinks]]` to sibling notes.
```

Property rules:
- `area` must equal the folder name.
- `type` and `status` take only the values listed. Do not invent new ones.
- `tags`: reuse existing tags. Check first with `rg -h "^tags:" ~/Desktop/Vault/Research | sort | uniq -c | sort -rn`. Do not tag the area or type again, and do not add a generic `research` tag, since the folder and properties already say that.
- `status: draft` while the user is still iterating, `stable` once they accept it, `superseded` when a newer note replaces it.

## Iterating on an existing note

Most of the work happens after the first write. The user refines scope, reverses a decision, or the investigation turns up something that invalidates an earlier conclusion. **While a note from this skill exists in the conversation, that note is the deliverable, not the chat.**

For every subsequent turn on the same topic:

1. **Write back in the same turn.** The moment a conclusion changes, edit the note. Do not batch changes, do not wait to be asked, and never let a design decision exist only in the terminal. Use `Edit` for a localised change and `Write` for a restructure.
2. **Update the note before printing the terminal summary**, so the summary describes what is on disk.
3. **Bump `updated:`.** Leave `date:` as the creation date.
4. **Record reversals explicitly.** When a recommendation is dropped, say what it was, what evidence was cited for it, and why that evidence does not hold. A short "What changed and why" section near the top does this well.
5. **Move retracted material rather than erasing it.** If an approach is deferred, give it a named later phase with the conditions under which it becomes live again.
6. **Keep the Answer in sync.** A stale conclusion on top of updated findings is worse than no note.
7. **Promote resolved open questions.** Move the answer into Findings, remove the question, and log it under "What changed and why".
8. **Moving or renaming a note** (including changing its area): update `area:` and fix inbound `[[wikilinks]]` in the same turn.

### Plan mode

Plan mode permits writing only to the plan file, so the vault write will fail. Do not silently skip it:

- Say in the turn that the vault note cannot be written yet and why.
- Keep the plan file and the note aligned in content.
- Write the note as the first action after plan mode ends.

## Rules

- **Cite or mark.** Anything verified gets a `file:line` or a named document. Anything inferred is labelled as an assumption. Never blur the two.
- **Conclusion first.** The Answer section is the deliverable; Findings are the evidence for it.
- **Never write secrets, tokens, credentials or customer data into the vault.** It is a plain-text folder on a desktop. Refer to people by role.
- **No file, no claim of one.** State the full path you wrote.
- **Stay portable.** Plain Markdown, YAML frontmatter and `[[wikilinks]]` only. No Obsidian-only syntax (dataview queries, embeds) inside notes.

## Terminal output

After writing, print 5–10 lines: the conclusion, the two or three findings that would change a decision, and the note path (`Research/<Area>/…`). Nothing else. The note holds the detail.
