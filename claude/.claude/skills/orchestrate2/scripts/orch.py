#!/usr/bin/env python3
"""Deterministic engine for the orchestrate2 skill. No LLM involved.

It launches workers and validators, reads their labels, watches PRs and CI,
wakes workers with findings, retargets stacked PRs, cleans up completed tasks
and totals the cost. It only returns to the LLM for things the LLM must do:
Jira transitions (MCP) and escalations to the user.

Commands (run from anywhere inside the repo):
  init <tasks.json> [--cap N] [--base BRANCH] [--context FILE.md]   create a run; tasks may carry status/prs/jira/worker_name/hold to adopt work in progress
  watch [--every SEC]                           loop until the LLM is needed; prints JSON and exits
  tick                                          one pass (same output as watch, no loop)
  status                                        table of tasks, rounds, PRs, tokens
  set-jira KEY in_progress|in_review|done|failed:<state>
  retry KEY "message"                           reset rounds and wake the worker (after `stuck`)
  cost [--repo PATH] [--since EPOCH]            token totals (dollars if prices.json is filled)
  adopt-v1 <v1 state.json> [--out tasks.json]   convert an orchestrate (v1) run into a tasks.json for init
  hold KEY "reason" | hold KEY --release        keep a merged task from going Done / being cleaned up (external condition)
  start "<request>"                             launch the orchestrator agent in a new tab (used by the skill)
  signal impl_done|blocked|pr_open|reject       how worker/validator sessions report (labels + messages)

Env: ORCH_DRY=1 prints launches instead of running them.
"""
import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(HERE))
import hc_tab  # noqa: E402

DRY = bool(os.environ.get("ORCH_DRY"))
MAX_ROUNDS = 3
PROJECTS = pathlib.Path.home() / ".claude" / "projects"
JIRA_ORDER = {"none": 0, "in_progress": 1, "in_review": 2, "done": 3}
LIVE = ("implementing", "validating")
PARENT_OK = ("pr_open", "in_review", "merged", "completed")

LEASE = "Bash(git push --force-with-lease*)"  # rebase grant; a deny in ~/.claude/settings.json still wins, it must only match plain --force/-f
SCRIPTS = "Bash(python3 *)"  # run-checks, orch.py signal
WORKER_ALLOW = f"Bash(git *),Bash(gh pr view*),Bash(gh pr checks*),Bash(gh run *),Bash(make *),{SCRIPTS},{LEASE}"
VALIDATOR_ALLOW = f"Bash(git *),Bash(gh *),Bash(make *),{SCRIPTS},{LEASE},mcp__github__create_pull_request"
ORCH_ALLOW = f"Bash(python3 *),Bash(git *),Bash(gh *),Bash(hc *),Bash(cat *),Bash(ls *),mcp__atlassian-mcp-server,mcp__claude_ai_Atlassian_MCP"
# Plain --force / -f and merging stay denied; --force-with-lease (above) is the only allowed force.
DENY = ("Bash(gh pr merge*),Bash(gh pr ready*),Bash(git push --force *),Bash(git push -f *),"
        "Bash(git push origin --delete*),Bash(git branch -D*)")
WORKER_DENY = DENY + ",Bash(gh pr create*)"
VALIDATOR_DENY = DENY + ",Edit,Write,NotebookEdit"
ORCH_DENY = DENY + ",Edit,Write,NotebookEdit"


# ---------------------------------------------------------------- helpers
def sh(args, cwd=None, input=None):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, input=input)


def out_json(r):
    try:
        return json.loads(r.stdout)
    except Exception:
        return None


def repo_root(start=None):
    r = sh(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=start)
    if r.returncode:
        sys.exit("not inside a git repo")
    return str(pathlib.Path(r.stdout.strip()).parent)


def orch_dir(repo):
    return pathlib.Path(repo) / ".git" / "orchestrate2"


def state_path(repo):
    d = orch_dir(repo)
    latest = (d / "latest").read_text().strip() if (d / "latest").exists() else None
    if not latest:
        sys.exit("no run yet: run `orch.py init` first")
    return d / latest / "state.json"


def load(repo):
    return json.loads(state_path(repo).read_text())


def save(st):
    p = state_path(st["repo"])
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1))
    tmp.replace(p)


def slug(path):
    return re.sub(r"[/.]", "-", str(path))


def wt_path(st, key):
    return f"{st['repo']}/.claude/worktrees/{key}"


def num(key):
    """The numeric part of a ticket key (TU-51204 -> 51204): the only part that matters when reading tabs."""
    m = re.findall(r"\d+", key)
    return m[-1] if m else key.lower()


STOP = {"a", "an", "the", "to", "for", "of", "in", "on", "and", "with", "via", "from", "into"}


def slug_words(t):
    """Up to 3 lowercase words describing the task: the orchestrator's `label`, else the title without filler."""
    base = t.get("label") or ""
    if base:
        words = [w for w in re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-").split("-") if w]
    else:
        words = re.findall(r"[a-z0-9]+", (t.get("title") or "").lower())
        words = [w for w in words if w not in STOP] or words
    return words[:3]


def tab_label(t):
    """Tab name: the ticket number, an underscore, then 3 words of the task, dash-separated.
    e.g. 'add acceptance tests' + TU-51204 -> '51204_add-acceptance-tests'."""
    slug = "-".join(slug_words(t))
    return f"{num(t['key'])}_{slug}" if slug else num(t["key"])


ROLE_CODE = {"worker": "w", "validator": "v"}  # short role suffix in terminal names; add new roles here


def tname(t, role):
    """Terminal name (what the sidebar shows): <number>_<task words>_<role code>, e.g. 51204_add-acceptance-tests_w.
    hc limits names to 32 chars of a-z 0-9 - _, so whole words are dropped from the end of the slug until it
    fits. If a live terminal already has the name (same number in two projects) the full lowercase key is used
    instead of the number."""
    key, name = t["key"], None
    code = ROLE_CODE.get(role, role)
    room = max(len(c) for c in list(ROLE_CODE.values()) + [code])
    for n in (num(key), key.lower()):
        budget = 32 - len(n) - 2 - room
        words = []
        for w in slug_words(t):
            if len("-".join(words + [w])) <= budget:
                words.append(w)
            else:
                break
        slug = "-".join(words)
        name = f"{n}_{slug}_{code}" if slug else f"{n}_{code}"
        if hc_get(name) is None:
            return name
    return name


def wname(t):
    """Worker terminal name; adopted tasks keep the name their old session already has."""
    return t.get("worker_name") or f"{t['key'].lower()}-w"  # legacy name of tasks launched before explicit roles


def vname(t):
    return t.get("validator_name") or f"{t['key'].lower()}-v"


# ---------------------------------------------------------------- hc
def hc_get(name):
    r = sh(["hc", "get", name, "--json"])
    return out_json(r) if r.returncode == 0 else None


def hc_live():
    r = sh(["hc", "ls", "--json"])
    d = out_json(r) or {}
    return {t["name"]: t for t in d.get("terminals", []) if t.get("name")}


def hc_labels(name):
    d = hc_get(name)
    return (d or {}).get("labels", {}) or {}


def hc_label(name, **kv):
    if DRY:
        return
    sh(["hc", "label", name] + [f"{k}={v}" for k, v in kv.items()])


def hc_send(name, text):
    if DRY:
        print(f"DRY hc send {name}: {text[:100]!r}")
        return
    sh(["hc", "send", name, text])


def hc_start(st, name, cwd, argv, tab_label, beside=None):
    """Start a terminal, show it as a tab (or as a second pane next to `beside`), wait until Claude is ready.
    Returns address."""
    cmd = ["hc", "run", "--bg", "--json", "--name", name, "--cwd", cwd,
           "--outlive-parent", "--no-notify"] + argv
    if DRY:
        print("DRY " + " ".join(cmd))
        return "tDRY"
    r = sh(cmd)
    d = out_json(r)
    if r.returncode or not d:
        raise RuntimeError(f"hc run failed: {r.stderr.strip() or r.stdout.strip()}")
    addr = d["address"]
    try:
        if beside:
            try:
                hc_tab.add_pane(addr, beside)
            except BaseException:  # the worker's tab is gone: fall back to a tab of its own
                hc_tab.add(addr, tab_label, st["anchor"])
        else:
            hc_tab.add(addr, tab_label, st["anchor"])
    except BaseException as e:  # tab is cosmetic; the session still runs
        print(f"warning: could not show {name}: {e}", file=sys.stderr)
    sh(["hc", "wait", name, "--timeout", "60"])
    return addr


def hc_stop(name):
    """Close tab, end and remove the terminal."""
    d = hc_get(name)
    if not d:
        return
    if DRY:
        print(f"DRY stop {name}")
        return
    try:
        hc_tab.remove(d["address"])
    except BaseException as e:
        print(f"warning: could not remove tab for {name}: {e}", file=sys.stderr)
    sh(["hc", "kill", name])
    sh(["hc", "rm", name])


# ---------------------------------------------------------------- github
def prs(st, t):
    r = sh(["gh", "pr", "list", "--state", "all", "--search", f"{t['key']} in:title",
            "--json", "number,title,state,isDraft,baseRefName,headRefName,headRefOid,url,statusCheckRollup",
            "--limit", "20"], cwd=st["repo"])
    data = out_json(r)
    if not isinstance(data, list):
        return []
    return [p for p in data if t["key"].lower() in p["headRefName"].lower()
            or t["key"].lower() in (p.get("title") or "").lower()]


def ci_state(pr, ignore=()):
    checks = [c for c in (pr.get("statusCheckRollup") or [])
              if not any(i.lower() in (c.get("name") or c.get("context") or "").lower() for i in ignore)]
    if not checks:
        return "none"
    pend = False
    for c in checks:
        res = (c.get("conclusion") or c.get("state") or "").upper()
        if res in ("FAILURE", "TIMED_OUT", "CANCELLED", "ERROR", "STARTUP_FAILURE", "ACTION_REQUIRED"):
            return "failed"
        if c.get("status", "COMPLETED").upper() != "COMPLETED" or res in ("PENDING", "EXPECTED", ""):
            pend = True
    return "pending" if pend else "passed"


def failing_logs(st, branch):
    r = sh(["gh", "run", "list", "--branch", branch, "--status", "failure", "--limit", "1",
            "--json", "databaseId"], cwd=st["repo"])
    d = out_json(r) or []
    if not d:
        return "(no failed run found; run `gh pr checks` to see what failed)"
    log = sh(["gh", "run", "view", str(d[0]["databaseId"]), "--log-failed"], cwd=st["repo"]).stdout
    return "\n".join(log.splitlines()[-60:])


# ---------------------------------------------------------------- prompts
def ticket_block(t):
    return f"Ticket {t['key']}: {t['title']}\n\n{t.get('description', '').strip()}"


def launch_worker(st, t, base, stacked):
    key, repo = t["key"], st["repo"]
    name = t.get("worker_name") or tname(t, "worker")
    t["worker_name"] = name
    if stacked:
        sh(["git", "fetch", "origin", base], cwd=repo)
        t["fork_point"] = sh(["git", "rev-parse", f"origin/{base}"], cwd=repo).stdout.strip()
        t["stacked_on"] = base
    argv = ["claude", "--worktree", key, "--permission-mode", "auto", "--add-dir", repo,
            "--agent", "worker",
            "--allowedTools", WORKER_ALLOW, "--disallowedTools", WORKER_DENY]
    hc_start(st, name, repo, argv, tab_label(t))
    hc_label(name, role="worker", task=key, state="working", round=t["round"])
    hc_send(name, f"{ticket_block(t)}\n\nBranch (rename your worktree branch to this): {t['branch']}\n"
                  f"Base: origin/{base}{' (a parent task branch: stacked)' if stacked else ''}\n"
                  f"Your terminal name: {name}\nStart now.")
    t["base"] = base


def launch_validator(st, t):
    key = t["key"]
    name = t.get("validator_name") or tname(t, "validator")
    t["validator_name"] = name
    worker = wname(t)
    argv = ["claude", "--permission-mode", "auto", "--add-dir", st["repo"],
            "--agent", "validator",
            "--allowedTools", VALIDATOR_ALLOW, "--disallowedTools", VALIDATOR_DENY]
    w = hc_get(worker)
    hc_start(st, name, wt_path(st, key), argv, tab_label(t), beside=(w or {}).get("address"))
    hc_label(name, role="validator", task=key, state="working", round=t["round"])
    existing = f" A PR already exists ({t['prs']}): do not open another, just report." if t.get("prs") else ""
    hc_send(name, f"{ticket_block(t)}\n\nBranch: {t['branch']}  Base: origin/{t['base']}\n"
                  f"Worker terminal: {worker}   Your terminal: {name}   Round: {t['round']}/{MAX_ROUNDS}.{existing}\nStart now.")


SIGNAL = "python3 ~/.claude/skills/orchestrate2/scripts/orch.py signal impl_done"


def wake_worker(st, t, text):
    """Send text to the task's worker. If its session is gone (restart, adopted run), start a new
    worker in the existing worktree first, so wake-ups never get lost."""
    name = wname(t)
    text = f"{text}\n(When fixed and pushed, run: {SIGNAL})"
    if name not in hc_live() and not DRY:
        wt = wt_path(st, t["key"])
        if not os.path.isdir(wt):
            raise RuntimeError(f"worker {name} is gone and {wt} does not exist")
        argv = ["claude", "--permission-mode", "auto", "--add-dir", st["repo"], "--agent", "worker",
                "--allowedTools", WORKER_ALLOW, "--disallowedTools", WORKER_DENY]
        hc_start(st, name, wt, argv, tab_label(t))
        text = (f"{ticket_block(t)}\n\nThis is a continuation: branch {t['branch']} already exists in this worktree "
                f"(base origin/{t.get('base', st['base'])}). Do NOT reset or rename it. Your terminal name: {name}.\n\n{text}")
    hc_label(name, role="worker", task=t["key"], state="fixing", round=t["round"])
    hc_send(name, text)


# ---------------------------------------------------------------- engine
def notify(out, t, code, msg):
    seen = t.setdefault("notified", [])
    if code in seen:
        return
    seen.append(code)
    out["notify"].append({"key": t["key"], "code": code, "message": msg})


def want_jira(out, t, to):
    if not t.get("jira_enabled", True) or (to == "done" and t.get("hold")):
        return
    if JIRA_ORDER.get(t["jira"], 0) >= JIRA_ORDER[to] or t.get("jira_failed", {}).get(to):
        return
    if any(j["key"] == t["key"] and j["to"] == to for j in out["jira"]):
        return
    out["jira"].append({"key": t["key"], "to": to, "assign": to == "in_progress"})


def stuck(out, t, why):
    t["status"] = "stuck"
    notify(out, t, f"stuck-{t['round']}", f"{why} (after {t['round']} failed rounds). "
           f"Use `orch.py retry {t['key']} \"<guidance>\"` to continue.")


def parent_state(st, t):
    """-> (ready, base, stacked). Stacks on the single unmerged parent; waits if more than one."""
    unmerged, base = [], st["base"]
    for p in t["parents"]:
        ps = st["tasks"][p]
        if ps["status"] not in PARENT_OK:
            return False, None, False
        if t.get("wait_for_merge") and ps["status"] not in ("merged", "completed"):
            return False, None, False
        if ps["status"] in ("pr_open", "in_review"):
            unmerged.append(ps)
    if len(unmerged) > 1:
        return False, None, False
    if unmerged:
        return True, unmerged[0]["branch"], True
    return True, base, False


def retarget(st, t, out):
    """Parent merged: point the child's PR at the base and rebase it onto it."""
    parent = st["tasks"].get(t["parents"][0]) if t["parents"] else None
    if not t.get("stacked_on") or t.get("retargeted") or not parent or parent["status"] not in ("merged", "completed"):
        return
    repo, wt, base = st["repo"], wt_path(st, t["key"]), st["base"]
    for n in t.get("prs", []):
        sh(["gh", "pr", "edit", str(n), "--base", base], cwd=repo)
    sh(["git", "fetch", "origin"], cwd=wt)
    r = sh(["git", "rebase", "--onto", f"origin/{base}", t["fork_point"]], cwd=wt)
    if r.returncode:
        sh(["git", "rebase", "--abort"], cwd=wt)
        t["retargeted"] = True
        notify(out, t, "rebase-conflict", f"Rebasing {t['key']} onto {base} conflicts. PR base was retargeted, "
               f"the branch was not changed. Resolve it or `retry` with guidance.")
        return
    sh(["git", "push", "--force-with-lease", "origin", t["branch"]], cwd=wt)
    t["retargeted"] = True


def pr_phase(st, t, out):
    pl = prs(st, t)
    if not pl:
        return
    t["prs"] = [p["number"] for p in pl]
    open_ = [p for p in pl if p["state"] == "OPEN"]
    merged = [p for p in pl if p["state"] == "MERGED"]
    if not open_ and merged:
        t["status"] = "merged"
        want_jira(out, t, "done")
        return
    if not open_ and not merged:
        notify(out, t, "pr-closed", f"All PRs of {t['key']} were closed without merging.")
        return
    if any(not p["isDraft"] for p in open_):
        if t["status"] == "pr_open":
            t["status"] = "in_review"
        want_jira(out, t, "in_review")
    if t["status"] in ("pr_open", "in_review"):
        retarget(st, t, out)
    for p in open_:
        if ci_state(p, st.get("ignore_checks", [])) == "failed" and p["headRefOid"] != t.get("ci_handled") and t["status"] in ("pr_open", "in_review"):
            t["ci_handled"] = p["headRefOid"]
            t["round"] += 1
            if t["round"] >= MAX_ROUNDS:
                stuck(out, t, "CI keeps failing")
                return
            t["status"] = "implementing"
            wake_worker(st, t, f"[orchestrator] CI failed on PR #{p['number']}. Fix it, commit, push, then set your "
                           f"label state=impl_done. Failing log:\n{failing_logs(st, t['branch'])}")
            return


def tick(st):
    out = {"jira": [], "notify": [], "finished": False}
    tasks = st["tasks"]
    live = hc_live()
    running = sum(1 for t in tasks.values() if t["status"] in LIVE)
    for key, t in tasks.items():
        try:
            s = t["status"]
            if s == "pending":
                ok, base, stacked = parent_state(st, t)
                if ok and running < st["cap"]:
                    launch_worker(st, t, base, stacked)
                    t["status"] = "implementing"
                    running += 1
                    want_jira(out, t, "in_progress")
            elif s == "implementing":
                w = wname(t)
                if hc_labels(w).get("state") == "impl_done":
                    t["status"] = "validating"
                    launch_validator(st, t)
                elif w not in live and not DRY:
                    notify(out, t, "worker-gone", f"The worker terminal for {key} is gone without finishing.")
                want_jira(out, t, "in_progress")
            elif s == "validating":
                v = vname(t)
                state = hc_labels(v).get("state")
                if state == "pr_open":
                    t["status"] = "pr_open"
                    hc_stop(v)
                elif state == "rejected":
                    t["round"] += 1
                    hc_stop(v)
                    if t["round"] >= MAX_ROUNDS:
                        stuck(out, t, "Validation keeps failing")
                    else:
                        t["status"] = "implementing"
                elif v not in live and not DRY:
                    notify(out, t, "validator-gone", f"The validator terminal for {key} is gone without a verdict.")
                want_jira(out, t, "in_progress")
            elif s in ("pr_open", "in_review"):
                pr_phase(st, t, out)
            elif s == "merged":
                pr_phase(st, t, out)
                if t["status"] == "merged" and not t.get("hold") and (not t.get("jira_enabled", True) or t["jira"] == "done"):
                    complete(st, t, out)
        except Exception as e:  # keep the watch alive; tell the LLM once
            notify(out, t, "engine-error", f"{type(e).__name__}: {e}")
    if all(t["status"] == "completed" for t in tasks.values()):
        out["finished"] = True
    return out


def complete(st, t, out):
    """All PRs merged and Jira Done: free the worktree, tab and sessions."""
    key, wt = t["key"], wt_path(st, t["key"])
    if os.path.isdir(wt):
        dirty = sh(["git", "status", "--porcelain"], cwd=wt).stdout.strip()
        sh(["git", "fetch", "origin"], cwd=wt)
        unpushed = sh(["git", "rev-list", f"origin/{t['branch']}..HEAD", "--count"], cwd=wt).stdout.strip()
        if dirty or unpushed not in ("0", ""):
            notify(out, t, "cleanup-blocked", f"{key} is completed but its worktree has "
                   f"{'uncommitted changes' if dirty else 'unpushed commits'}; left in place.")
            t["status"] = "completed"
            return
    hc_stop(wname(t))
    hc_stop(vname(t))
    if os.path.isdir(wt) and not DRY:
        sh(["git", "worktree", "remove", wt], cwd=st["repo"])
    t["status"] = "completed"


# ---------------------------------------------------------------- cost
def load_prices():
    p = SKILL / "prices.json"
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def session_tokens(project_dir, since=0):
    tot = {}
    for f in pathlib.Path(project_dir).glob("*.jsonl"):
        if f.stat().st_mtime < since:
            continue
        seen = {}
        for line in f.open():
            try:
                d = json.loads(line)
            except Exception:
                continue
            m = d.get("message")
            if isinstance(m, dict) and isinstance(m.get("usage"), dict):
                seen[m.get("id") or line[:64]] = (m.get("model", "?"), m["usage"])
        for model, u in seen.values():
            a = tot.setdefault(model, dict(input=0, output=0, cache_read=0, cache_write=0))
            a["input"] += u.get("input_tokens", 0)
            a["output"] += u.get("output_tokens", 0)
            a["cache_read"] += u.get("cache_read_input_tokens", 0)
            a["cache_write"] += u.get("cache_creation_input_tokens", 0)
    return tot


def dollars(tok, prices):
    total = 0.0
    for model, a in tok.items():
        p = prices.get(model)
        if not p or any(p.get(k) is None for k in ("input", "output", "cache_read", "cache_write")):
            return None
        total += sum(a[k] * p[k] for k in a) / 1e6
    return total


def cost_rows(repo, since=0, keys=None):
    base = slug(repo)
    rows = {}
    for d in sorted(PROJECTS.glob(base + "--claude-worktrees-*")):
        k = d.name.split("--claude-worktrees-")[1]
        if keys is None or k in keys:
            rows[k] = session_tokens(d, since)
    rows["(orchestrator + other sessions in repo)"] = session_tokens(PROJECTS / base, since)
    return rows


def fmt_tokens(tok):
    s = {k: sum(a[k] for a in tok.values()) for k in ("input", "output", "cache_read", "cache_write")}
    return f"in {s['input']:>9,}  out {s['output']:>8,}  cache-r {s['cache_read']:>12,}  cache-w {s['cache_write']:>9,}"


def print_cost(rows):
    prices = load_prices()
    grand = {}
    any_dollars = True
    for k, tok in rows.items():
        dl = dollars(tok, prices)
        any_dollars &= dl is not None
        print(f"{k:42} {fmt_tokens(tok)}" + (f"  ${dl:,.2f}" if dl is not None else ""))
        for m, a in tok.items():
            g = grand.setdefault(m, dict(input=0, output=0, cache_read=0, cache_write=0))
            for kk in a:
                g[kk] += a[kk]
    dl = dollars(grand, prices)
    print(f"{'TOTAL':42} {fmt_tokens(grand)}" + (f"  ${dl:,.2f}" if dl is not None else ""))
    if dl is None:
        print("(dollars need rates: fill prices.json next to SKILL.md, per million tokens, per model id)")


# ---------------------------------------------------------------- commands
def cmd_init(a):
    repo = repo_root()
    tasks = json.loads(pathlib.Path(a.tasks).read_text())
    base = a.base or (sh(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=repo)
                      .stdout.strip().replace("origin/", "") or "main")
    keys = {t["key"] for t in tasks}
    ok = {"pending", "implementing", "validating", "pr_open", "in_review", "merged", "completed", "stuck"}
    for t in tasks:
        if t.get("status", "pending") not in ok:
            sys.exit(f"{t['key']}: bad status {t['status']}")
        for p in t.get("parents", []):
            if p not in keys:
                sys.exit(f"{t['key']}: unknown parent {p}")
    seen, stack = set(), set()

    def visit(k):
        if k in stack:
            sys.exit(f"dependency cycle at {k}")
        if k in seen:
            return
        stack.add(k)
        for p in next(t for t in tasks if t["key"] == k).get("parents", []):
            visit(p)
        stack.discard(k)
        seen.add(k)
    for k in keys:
        visit(k)
    if not DRY:
        if sh(["git", "status", "--porcelain"], cwd=repo).stdout.strip():
            sys.exit("main checkout is not clean; commit or stash first")
        sh(["git", "fetch", "origin"], cwd=repo)
        sh(["git", "switch", base], cwd=repo)
        r = sh(["git", "pull", "--ff-only"], cwd=repo)
        if r.returncode:
            sys.exit(f"git pull --ff-only failed: {r.stderr.strip()}")
    run = time.strftime("%Y%m%d-%H%M%S")
    d = orch_dir(repo) / run
    d.mkdir(parents=True)
    st = {"run": run, "repo": repo, "base": base, "cap": a.cap, "started": time.time(),
          "anchor": os.environ.get("HC_TERMINAL_ID", ""), "ignore_checks": a.ignore_check or [], "tasks": {}}
    for t in tasks:
        k = t["key"]
        st["tasks"][k] = {
            "key": k, "title": t["title"], "description": t.get("description", ""), "branch": t["branch"],
            "parents": t.get("parents", []), "status": t.get("status", "pending"), "round": 0, "prs": t.get("prs", []),
            "jira": t.get("jira", "none"), "worker_name": t.get("worker_name"), "hold": t.get("hold"), "wait_for_merge": bool(t.get("wait_for_merge")),
            "base": t.get("base", base), "label": t.get("label", ""), "jira_enabled": bool(re.match(r"^[A-Z][A-Z0-9]+-\d+$", k)) and not k.startswith("NOJIRA"),
        }
    (orch_dir(repo) / "latest").write_text(run)
    if a.context:
        (d / "context.md").write_text(pathlib.Path(a.context).read_text())
    save(st)
    print(f"run {run} created with {len(tasks)} tasks, base {base}, cap {a.cap}")


def cmd_tick(a, loop=False):
    repo = repo_root()
    while True:
        st = load(repo)
        out = tick(st)
        save(st)
        if out["jira"] or out["notify"] or out["finished"] or not loop:
            if out["finished"]:
                out["cost_hint"] = "run `orch.py cost --since <started>` for the final total"
            print(json.dumps(out, indent=1))
            return
        time.sleep(a.every)


def cmd_status(a):
    st = load(repo_root())
    rows = cost_rows(st["repo"], st["started"], set(st["tasks"]))
    print(f"run {st['run']}  base {st['base']}  cap {st['cap']}")
    for k, t in st["tasks"].items():
        tok = rows.get(k, {})
        n = sum(x["output"] + x["input"] + x["cache_write"] for x in tok.values())
        print(f"{k:10} {t['status']:12} round {t['round']}/{MAX_ROUNDS}  PRs {t['prs'] or '-'}  jira {t['jira']:11} "
              f"parents {','.join(t['parents']) or '-'}  worker {wname(t)}  ~{n/1000:,.0f}k new tokens")


def cmd_set_jira(a):
    st = load(repo_root())
    t = st["tasks"][a.key]
    if a.to.startswith("failed:"):
        t.setdefault("jira_failed", {})[a.to.split(":", 1)[1]] = True
    else:
        t["jira"] = a.to
    save(st)


def cmd_retry(a):
    st = load(repo_root())
    t = st["tasks"][a.key]
    t["round"], t["status"], t["notified"] = 0, "implementing", []
    wake_worker(st, t, f"[orchestrator] The user says: {a.message}")
    save(st)


def self_term():
    t = os.environ.get("HC_TERMINAL_ID")
    if not t:
        sys.exit("HC_TERMINAL_ID is not set: run this from inside a Herdr terminal")
    return t


def cmd_signal(a):
    me = self_term()
    if a.what == "impl_done":
        hc_label(me, state="impl_done")
    elif a.what == "blocked":
        hc_label(me, state="blocked")
    elif a.what == "pr_open":
        hc_label(me, state="pr_open", **({"pr": a.pr} if a.pr else {}))
    elif a.what == "reject":
        if not (a.worker and a.findings):
            sys.exit("reject needs --worker and --findings")
        hc_label(a.worker, state="fixing")  # before the message, so the engine never relaunches the validator early
        hc_send(a.worker, f"[validator] {a.findings}")
        hc_label(me, state="rejected")
    print(f"signalled {a.what}")


def cmd_start(a):
    """Launch the orchestrator agent in its own tab (or message the one that is running)."""
    repo = repo_root()
    name = ("orchestrator-" + re.sub(r"[^a-z0-9]+", "-", os.path.basename(repo).lower()).strip("-"))[:32]
    request = a.request or "continue"
    if name in hc_live():
        hc_send(name, request)
        print(f"orchestrator already running in terminal {name}; sent it your message")
        return
    argv = ["claude", "--agent", "orchestrator", "--permission-mode", "auto", "--add-dir", repo,
            "--allowedTools", ORCH_ALLOW, "--disallowedTools", ORCH_DENY]
    hc_start({"anchor": os.environ.get("HC_TERMINAL_ID", "")}, name, repo, argv, "orchestrator")
    hc_label(name, role="orchestrator")
    hc_send(name, request)
    print(f"orchestrator started in a new tab ({name}); continue there")


def cmd_adopt_v1(a):
    """Convert a v1 (orchestrate) state.json into a tasks.json for `init`, keeping what is still open."""
    repo = a.repo or repo_root()
    v1 = json.loads(pathlib.Path(a.state).read_text())["tasks"]
    mine = {k: t for k, t in v1.items() if t.get("repo", repo) in (repo, os.path.basename(repo))}

    def parents(t):
        p = t.get("parent") or []
        return [p] if isinstance(p, str) else list(p)
    open_keys = {k for k, t in mine.items() if t["status"] != "merged" or os.path.isdir(f"{repo}/.claude/worktrees/{k}")}
    needed = set(open_keys)
    for k in open_keys:
        needed |= {p for p in parents(mine[k]) if p in mine}
    out = []
    done = {k for k in needed if mine[k]["status"] == "merged" and not os.path.isdir(f"{repo}/.claude/worktrees/{k}")}
    needed -= done  # fully finished and cleaned: nothing left to do, and no need to list them as parents
    for k in sorted(needed):
        t = mine[k]
        wt = os.path.isdir(f"{repo}/.claude/worktrees/{k}")
        status = {"pending": "pending", "running": "implementing", "done": "pr_open"}.get(t["status"])
        if t["status"] == "merged":
            status = "merged" if wt else "completed"
        o = {"key": k, "title": t.get("title") or "", "description": t.get("description") or "",
             "branch": t.get("branch") or "", "parents": [p for p in parents(t) if p in needed],
             "status": status, "jira": t.get("jira") or "none",
             "wait_for_merge": len(parents(t)) > 1}  # v1 waited for merge on multi-parent tasks
        if wt and t.get("name"):
            o["worker_name"] = t["name"]
        if not o["branch"] or not o["title"]:
            o["_todo"] = "fill branch/title/description from Jira before init"
        out.append(o)
    pathlib.Path(a.out).write_text(json.dumps(out, indent=1))
    print(f"{len(out)} tasks written to {a.out} ({sum(1 for o in out if '_todo' in o)} need branch/title from Jira)")


def cmd_hold(a):
    st = load(repo_root())
    st["tasks"][a.key]["hold"] = None if a.release else (a.reason or "on hold")
    save(st)
    print(("released " if a.release else "held ") + a.key)


def cmd_cost(a):
    repo = a.repo or repo_root()
    print_cost(cost_rows(os.path.realpath(repo), a.since or 0))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("init"); p.add_argument("tasks"); p.add_argument("--cap", type=int, default=4); p.add_argument("--base")
    p.add_argument("--ignore-check", action="append", help="substring of a CI check name that must not count as a failure (repeatable)")
    p.add_argument("--context", help="markdown with decisions carried over from a previous run; the orchestrator reads it")
    p.set_defaults(f=cmd_init)
    p = sp.add_parser("watch"); p.add_argument("--every", type=int, default=60); p.set_defaults(f=lambda a: cmd_tick(a, True))
    p = sp.add_parser("tick"); p.set_defaults(f=cmd_tick, every=0)
    sp.add_parser("status").set_defaults(f=cmd_status)
    p = sp.add_parser("set-jira"); p.add_argument("key"); p.add_argument("to"); p.set_defaults(f=cmd_set_jira)
    p = sp.add_parser("retry"); p.add_argument("key"); p.add_argument("message"); p.set_defaults(f=cmd_retry)
    p = sp.add_parser("signal"); p.add_argument("what", choices=["impl_done", "blocked", "pr_open", "reject"])
    p.add_argument("--pr"); p.add_argument("--worker"); p.add_argument("--findings"); p.set_defaults(f=cmd_signal)
    p = sp.add_parser("start"); p.add_argument("request", nargs="?"); p.set_defaults(f=cmd_start)
    p = sp.add_parser("adopt-v1"); p.add_argument("state"); p.add_argument("--out", default="tasks.json"); p.add_argument("--repo")
    p.set_defaults(f=cmd_adopt_v1)
    p = sp.add_parser("hold"); p.add_argument("key"); p.add_argument("reason", nargs="?"); p.add_argument("--release", action="store_true")
    p.set_defaults(f=cmd_hold)
    p = sp.add_parser("cost"); p.add_argument("--repo"); p.add_argument("--since", type=float); p.set_defaults(f=cmd_cost)
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
