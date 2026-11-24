#!/usr/bin/env python3
"""Detect a repo's project type (Go, TypeScript/JavaScript) and run its basic checks.

Usage: checks.py [--dir PATH] [--plan] [--fail-fast] [--tail N]

Order: lint, typecheck, test, build. Every step runs even if an earlier one fails
(unless --fail-fast) so one run reports everything. Output of passing steps is
dropped; failing steps print the last N lines (default 40). Exit 0 only if all pass.
--plan prints what would run without running it.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

TIMEOUT = 15 * 60


def read(path):
    try:
        return open(path).read()
    except OSError:
        return ""


def make_targets(d):
    text = read(os.path.join(d, "Makefile")) or read(os.path.join(d, "makefile"))
    return set(re.findall(r"^([A-Za-z0-9_.-]+)\s*:(?!=)", text, re.M))


def first(options, have):
    return next((o for o in options if o in have), None)


def go_steps(d):
    tg = make_targets(d)
    steps = []
    lint = first(["lint"], tg)
    steps.append(("lint", ["make", lint]) if lint else
                 ("lint", ["golangci-lint", "run"]) if shutil.which("golangci-lint") and (
                     any(os.path.exists(os.path.join(d, f)) for f in (".golangci.yml", ".golangci.yaml", ".golangci.toml")))
                 else ("lint", ["go", "vet", "./..."]))
    # `make test` often includes acceptance tests that need a running service: prefer unit.
    test = first(["test-unit", "test"], tg)
    steps.append(("test", ["make", test]) if test else ("test", ["go", "test", "./..."]))
    build = first(["build"], tg)
    steps.append(("build", ["make", build]) if build else ("build", ["go", "build", "./..."]))
    return steps


def js_runner(d):
    for lock, pm in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("bun.lockb", "bun"), ("bun.lock", "bun")):
        if os.path.exists(os.path.join(d, lock)):
            return pm
    return "npm"


def js_steps(d):
    try:
        scripts = json.loads(read(os.path.join(d, "package.json"))).get("scripts", {})
    except ValueError:
        scripts = {}
    pm = js_runner(d)
    run = lambda s: [pm, "run", s]  # noqa: E731
    steps = []
    lint = first(["lint", "eslint"], scripts)
    if lint:
        steps.append(("lint", run(lint)))
    tc = first(["typecheck", "type-check", "tsc", "check-types", "types"], scripts)
    if tc:
        steps.append(("typecheck", run(tc)))
    elif os.path.exists(os.path.join(d, "tsconfig.json")):
        steps.append(("typecheck", {"pnpm": ["pnpm", "exec", "tsc", "--noEmit"], "yarn": ["yarn", "tsc", "--noEmit"],
                                    "bun": ["bunx", "tsc", "--noEmit"]}.get(pm, ["npx", "tsc", "--noEmit"])))
    # prefer a non-interactive test script (CI=1 also stops watch mode)
    test = first(["test:ci", "test:unit", "test"], scripts)
    if test:
        steps.append(("test", run(test)))
    build = first(["build"], scripts)
    if build:
        steps.append(("build", run(build)))
    return steps


def detect(d):
    steps = []
    if os.path.exists(os.path.join(d, "go.mod")):
        steps += [(f"go:{n}", c) for n, c in go_steps(d)]
    if os.path.exists(os.path.join(d, "package.json")):
        steps += [(f"js:{n}", c) for n, c in js_steps(d)]
    order = {"lint": 0, "typecheck": 1, "test": 2, "build": 3}
    return sorted(steps, key=lambda s: order[s[0].split(":")[1]])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--fail-fast", action="store_true")
    ap.add_argument("--tail", type=int, default=40)
    a = ap.parse_args()
    d = os.path.abspath(a.dir)
    steps = detect(d)
    if not steps:
        print("NO CHECKS FOUND: no go.mod or package.json with lint/test/build scripts. "
              "Look at the repo's CLAUDE.md / README for how to verify it and say so in your report.")
        sys.exit(3)
    if a.plan:
        for n, c in steps:
            print(f"{n:14} {' '.join(c)}")
        return
    env = dict(os.environ, CI="1")
    results = []
    for name, cmd in steps:
        t0 = time.time()
        try:
            r = subprocess.run(cmd, cwd=d, env=env, capture_output=True, text=True, timeout=TIMEOUT)
            code, out = r.returncode, (r.stdout + r.stderr)
        except subprocess.TimeoutExpired:
            code, out = 124, f"timed out after {TIMEOUT}s"
        except FileNotFoundError as e:
            code, out = 127, f"command not found: {e}"
        results.append((name, cmd, code, time.time() - t0, out))
        print(f"{'PASS' if code == 0 else 'FAIL'}  {name:14} {' '.join(cmd)}  ({time.time() - t0:.0f}s)")
        if code and a.fail_fast:
            break
    failed = [r for r in results if r[2]]
    for name, cmd, code, _, out in failed:
        print(f"\n--- {name}: {' '.join(cmd)} (exit {code}), last {a.tail} lines ---")
        print("\n".join(out.splitlines()[-a.tail:]))
    print(f"\nSUMMARY: {len(results) - len(failed)}/{len(results)} passed" + (f"; FAILED: {', '.join(r[0] for r in failed)}" if failed else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
