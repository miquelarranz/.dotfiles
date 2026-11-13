---
name: investigate
description: Run a codebase/docs investigation and save the findings as an Obsidian
  note in ~/Desktop/Vault/Research. Use whenever the user says "investigate",
  "research", "dig into", or "look into" a topic, system, question or decision —
  and for any open-ended analysis whose output is a written finding rather than a
  code change. Do not use for one-off factual lookups or for tasks that end in an edit.
---

# Skill: Investigate

Produce a durable research note in the user's Obsidian vault, plus a short terminal summary.

## Destination

- Vault: `~/Desktop/Vault`
- Folder: `Research/` (flat — no subfolders). Create it if missing (`mkdir -p ~/Desktop/Vault/Research`).
- Filename: `YYYY-MM-DD <Title Case Topic>.md`, e.g. `2026-09-08 Consent Management Extensibility.md`
- Date is today's date, from the session context (`currentDate` in system prompt).

## Steps

### 1. Scope

Restate the question in one line. If two readings would lead to materially different investigations, ask — once, up front. Otherwise proceed.

### 2. Check for prior work

`ls ~/Desktop/Vault/Research/` before investigating. If a prior note covers this topic:
- **Same topic, same day** → update that note in place.
- **Same topic, earlier date** → new note, and `[[wikilink]]` both ways.
- **Adjacent topic** → `[[wikilink]]` it from the Related section.

This is what makes the vault a graph instead of a pile.

### 3. Investigate

Parallelise. In one message, launch up to 3 agents over independent areas (`Explore` for code location, `general-purpose` for open-ended questions). Pull docs from whatever is connected — Notion, Slack, Jira, web — rather than reasoning from memory.

### 4. Write the note

Use Obsidian Properties frontmatter, then the body:

```markdown
---
date: YYYY-MM-DD
tags: [research, <topic-tags>]
status: draft
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

`[[wikilinks]]` to sibling notes in Research/.
```

## Iterating on an existing note

Most of the work happens *after* the first write. The user refines scope, reverses a decision, or the investigation turns up something that invalidates an earlier conclusion. **The note is the deliverable for the whole conversation, not just the turn that created it.**

So, for every subsequent turn on the same topic:

1. **Write back in the same turn.** The moment a conclusion changes, edit the note. Do not batch changes for later, do not wait to be asked, and never let a design decision exist only in the terminal. Use `Edit` for a localised change and `Write` for a restructure.
2. **Update the note before printing the terminal summary**, so the summary describes what is actually on disk.
3. **Bump `updated: YYYY-MM-DD`** in the frontmatter. Leave `date:` as the original creation date.
4. **Record reversals explicitly, don't silently delete them.** When a recommendation is dropped, say what it was, what evidence was cited for it, and why that evidence does not hold. The note is re-read months later by someone who will otherwise re-propose the same rejected idea. A short "What changed and why" section near the top does this well.
5. **Move retracted material rather than erasing it.** If an axis, column or approach is deferred, give it a named later phase with the conditions under which it becomes live again.
6. **Keep the Answer section in sync.** It is the part that gets read. A stale conclusion on top of updated findings is worse than no note.
7. **Promote resolved open questions.** When a question in Open questions gets answered, move the answer into Findings and delete the question.

### Plan mode

Plan mode permits writing only to the plan file, so the vault write will fail. Do not silently skip it:

- Say in the turn that the vault note cannot be written yet and why.
- Keep the plan file and the note aligned in content — the plan file is the implementation-shaped version, the note is the research-shaped one.
- Write the note as the first action after plan mode ends.

## Rules

- **Cite or mark.** Anything verified gets a `file:line` or a named document. Anything inferred is labelled as an assumption, explicitly. Never blur the two — the note will be re-read months later with no memory of which was which.
- **Conclusion first.** The Answer section is the deliverable; Findings are the evidence for it.
- **Never write secrets, tokens, credentials or customer data into the vault.** It is a plain-text folder on a desktop. Refer to people by role.
- **No file, no claim of one.** State the full path you wrote.

## Terminal output

After writing, print 5–10 lines: the conclusion, the two or three findings that would change a decision, and the note path. Nothing else — the note holds the detail.
