---
name: run-checks
description: Run a repo's basic checks (lint, typecheck, tests, build) for Go and TypeScript/JavaScript projects and get a short pass/fail report. Use before handing off work or when validating a branch.
---

# Run checks

One command detects the project type and runs the checks, with output trimmed to what failed:

```bash
python3 ~/.claude/skills/run-checks/scripts/checks.py            # run from the repo or worktree root
python3 ~/.claude/skills/run-checks/scripts/checks.py --plan     # show what it would run
```

What it runs (steps in this order, all of them even if one fails):

| Stack | lint | typecheck | test | build |
|---|---|---|---|---|
| **Go** (`go.mod`) | `make lint`, else `golangci-lint run` (if configured), else `go vet ./...` | (part of build) | `make test-unit`, else `make test`, else `go test ./...` | `make build`, else `go build ./...` |
| **TS/JS** (`package.json`) | `lint` script | `typecheck`-like script, else `tsc --noEmit` | `test:ci`, `test:unit`, or `test` | `build` script |

The package manager comes from the lockfile (`pnpm`, `yarn`, `bun`, else `npm`). `CI=1` is set so test runners do not watch. It prefers `make test-unit` over `make test` because `test` often includes acceptance tests that need a running service.

Exit 0 means everything passed. Exit 1 prints, for each failing step, the last 40 lines of its output and a `SUMMARY:` line. Exit 3 means no checks were found: read the repo's CLAUDE.md or README for how to verify it and say so in your report; do not claim it passed.

If the script picks the wrong command for this repo (a monorepo package, a custom target), run the right one yourself and say which. Never skip a failing check by disabling it.
