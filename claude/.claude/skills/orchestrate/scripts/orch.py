#!/usr/bin/env python3
"""Deterministic engine for the orchestrate skill. No LLM involved.

It launches workers and validators, reads their labels, watches PRs and CI,
wakes workers with findings, retargets stacked PRs, cleans up completed tasks
and totals the cost. It only returns to the LLM for things the LLM must do:
Jira transitions (MCP) and escalations to the user.

Commands (run from the orchestrator's terminal; the run is bound to it. Add --run ID to pick another):
  repos [ROOT]                                  list the git repos under a folder (for matching repo names in tickets)
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
ATL = "mcp__claude_ai_Atlassian_MCP__"  # the claude.ai Atlassian connector; only what the orchestrator needs, no writes beyond Jira status/assignee
ATL_ALLOW = ",".join(ATL + n for n in (
    "getAccessibleAtlassianResources", "atlassianUserInfo", "getJiraIssue", "listJiraIssueComments",
    "searchJiraIssuesUsingJql", "searchConfluence", "getConfluenceContent", "discover", "executeRead",
    "transitionJiraIssue", "editJiraIssue"))
ORCH_ALLOW = f"Bash(python3 *),Bash(git *),Bash(gh *),Bash(hc *),Bash(cat *),Bash(ls *),{ATL_ALLOW}"
# Plain --force / -f and merging stay denied; --force-with-lease (above) is the only allowed force.
DENY = ("Bash(gh pr merge*),Bash(gh pr ready*),Bash(git push --force *),Bash(git push -f *),"
        "Bash(git push origin --delete*),Bash(git branch -D*)")
WORKER_DENY = DENY + ",Bash(gh pr create*)"
VALIDATOR_DENY = DENY + ",Edit,Write,NotebookEdit"
ORCH_DENY = DENY + ",Edit,Write,NotebookEdit," + ATL + "executeDestructive"


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


STATE_ROOT = pathlib.Path.home() / ".local" / "state" / "orchestrate" / "runs"  # own subfolder: old v1 state lives one level up
RUN_ARG = None


def orch_dir(repo):
    """Legacy location: runs started inside one repo live in <repo>/.git/orchestrate2 (name kept on purpose)."""
    return pathlib.Path(repo) / ".git" / "orchestrate2"


def state_file():
    """Which run? --run / $ORCH_RUN, else the run bound to this terminal (the orchestrator's own), else the
    legacy per-repo run of the repo you are standing in."""
    run = RUN_ARG or os.environ.get("ORCH_RUN")
    if run:
        p = STATE_ROOT / run / "state.json"
        if not p.exists():
            sys.exit(f"no run {run}")
        return p
    term = os.environ.get("HC_TERMINAL_ID")
    bind = STATE_ROOT / "by-terminal" / term if term else None
    if bind and bind.exists():
        p = STATE_ROOT / bind.read_text().strip() / "state.json"
        if p.exists():
            return p
    r = sh(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"])
    if r.returncode == 0:
        d = orch_dir(pathlib.Path(r.stdout.strip()).parent)
        if (d / "latest").exists():
            return d / (d / "latest").read_text().strip() / "state.json"
    sys.exit("no run found for this terminal: run `orch.py init` first (or pass --run <id>)")


def load():
    p = state_file()
    st = json.loads(p.read_text())
    st["_file"] = str(p)
    return st


def save(st):
    p = pathlib.Path(st["_file"])
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({k: v for k, v in st.items() if k != "_file"}, indent=1))
    tmp.replace(p)


def slug(path):
    return re.sub(r"[/.]", "-", str(path))


def trepo(st, t):
    """The task's repo checkout (runs from before multi-repo support have one repo for everything)."""
    return t.get("repo") or st["repo"]


def base_of(st, t):
    return (st.get("bases") or {}).get(trepo(st, t)) or st.get("base", "main")


def wt_path(st, t):
    return f"{trepo(st, t)}/.claude/worktrees/{t['key']}"


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
    if t.get("sub"):  # one ticket in several repos: the repo name leads, so the tabs are told apart
        words = [t["sub"]] + words[:2]
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
            "--limit", "20"], cwd=trepo(st, t))
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


def failing_logs(st, t):
    repo = trepo(st, t)
    r = sh(["gh", "run", "list", "--branch", t["branch"], "--status", "failure", "--limit", "1",
            "--json", "databaseId"], cwd=repo)
    d = out_json(r) or []
    if not d:
        return "(no failed run found; run `gh pr checks` to see what failed)"
    log = sh(["gh", "run", "view", str(d[0]["databaseId"]), "--log-failed"], cwd=repo).stdout
    return "\n".join(log.splitlines()[-60:])


# ---------------------------------------------------------------- prompts
def ticket_block(t):
    return f"Ticket {t['key']}: {t['title']}\n\n{t.get('description', '').strip()}"


def launch_worker(st, t, base, stacked):
    key, repo = t["key"], trepo(st, t)
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
    argv = ["claude", "--permission-mode", "auto", "--add-dir", trepo(st, t),
            "--agent", "validator",
            "--allowedTools", VALIDATOR_ALLOW, "--disallowedTools", VALIDATOR_DENY]
    w = hc_get(worker)
    hc_start(st, name, wt_path(st, t), argv, tab_label(t), beside=(w or {}).get("address"))
    hc_label(name, role="validator", task=key, state="working", round=t["round"])
    existing = f" A PR already exists ({t['prs']}): do not open another, just report." if t.get("prs") else ""
    hc_send(name, f"{ticket_block(t)}\n\nBranch: {t['branch']}  Base: origin/{t['base']}\n"
                  f"Worker terminal: {worker}   Your terminal: {name}   Round: {t['round']}/{MAX_ROUNDS}.{existing}\nStart now.")


SIGNAL = "python3 ~/.claude/skills/orchestrate/scripts/orch.py signal impl_done"


def wake_worker(st, t, text):
    """Send text to the task's worker. If its session is gone (restart, adopted run), start a new
    worker in the existing worktree first, so wake-ups never get lost."""
    name = wname(t)
    text = f"{text}\n(When fixed and pushed, run: {SIGNAL})"
    if name not in hc_live() and not DRY:
        wt = wt_path(st, t)
        if not os.path.isdir(wt):
            raise RuntimeError(f"worker {name} is gone and {wt} does not exist")
        argv = ["claude", "--permission-mode", "auto", "--add-dir", trepo(st, t), "--agent", "worker",
                "--allowedTools", WORKER_ALLOW, "--disallowedTools", WORKER_DENY]
        hc_start(st, name, wt, argv, tab_label(t))
        text = (f"{ticket_block(t)}\n\nThis is a continuation: branch {t['branch']} already exists in this worktree "
                f"(base origin/{t.get('base', base_of(st, t))}). Do NOT reset or rename it. Your terminal name: {name}.\n\n{text}")
    hc_label(name, role="worker", task=t["key"], state="fixing", round=t["round"])
    hc_send(name, text)


# ---------------------------------------------------------------- engine
def notify(out, t, code, msg):
    seen = t.setdefault("notified", [])
    if code in seen:
        return
    seen.append(code)
    out["notify"].append({"key": t.get("id") or t["key"], "code": code, "message": msg})


def want_jira(st, out, t, to):
    """Ask for a Jira transition. A ticket can span several repos (several tasks): it goes to In Review only when
    all its tasks are in review, and to Done only when all of them are merged and none is on hold."""
    if not t.get("jira_enabled", True):
        return
    sib = [x for x in st["tasks"].values() if x["key"] == t["key"]]
    if to == "in_review" and not all(x["status"] in ("in_review", "merged", "completed") for x in sib):
        return
    if to == "done" and (not all(x["status"] in ("merged", "completed") for x in sib) or any(x.get("hold") for x in sib)):
        return
    if JIRA_ORDER.get(t["jira"], 0) >= JIRA_ORDER[to] or t.get("jira_failed", {}).get(to):
        return
    if any(j["key"] == t["key"] and j["to"] == to for j in out["jira"]):
        return
    out["jira"].append({"key": t["key"], "to": to, "assign": to == "in_progress"})


def stuck(out, t, why):
    t["status"] = "stuck"
    notify(out, t, f"stuck-{t['round']}", f"{why} (after {t['round']} failed rounds). "
           f"Use `orch.py retry {t.get('id') or t['key']} \"<guidance>\"` to continue.")


def parent_state(st, t):
    """-> (ready, base, stacked). Stacks on the single unmerged parent of the same repo; a parent in another
    repo, or a task marked wait_for_merge, must be merged first. Waits if more than one parent is unmerged."""
    unmerged, base = [], base_of(st, t)
    for p in t["parents"]:
        ps = st["tasks"][p]
        if ps["status"] not in PARENT_OK:
            return False, None, False
        merged = ps["status"] in ("merged", "completed")
        if (t.get("wait_for_merge") or trepo(st, ps) != trepo(st, t)) and not merged:
            return False, None, False
        if not merged:
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
    repo, wt, base = trepo(st, t), wt_path(st, t), base_of(st, t)
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
        want_jira(st, out, t, "done")
        return
    if not open_ and not merged:
        notify(out, t, "pr-closed", f"All PRs of {t['key']} were closed without merging.")
        return
    if any(not p["isDraft"] for p in open_):
        if t["status"] == "pr_open":
            t["status"] = "in_review"
        want_jira(st, out, t, "in_review")
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
                           f"label state=impl_done. Failing log:\n{failing_logs(st, t)}")
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
                    want_jira(st, out, t, "in_progress")
            elif s == "implementing":
                w = wname(t)
                if hc_labels(w).get("state") == "impl_done":
                    t["status"] = "validating"
                    launch_validator(st, t)
                elif w not in live and not DRY:
                    notify(out, t, "worker-gone", f"The worker terminal for {key} is gone without finishing.")
                want_jira(st, out, t, "in_progress")
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
                want_jira(st, out, t, "in_progress")
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
    key, wt = t["key"], wt_path(st, t)
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
        sh(["git", "worktree", "remove", wt], cwd=trepo(st, t))
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


def cost_rows(repo, since=0, keys=None, other=True):
    base = slug(repo)
    rows = {}
    for d in sorted(PROJECTS.glob(base + "--claude-worktrees-*")):
        k = d.name.split("--claude-worktrees-")[1]
        if keys is None or k in keys:
            rows[k] = session_tokens(d, since)
    if other:
        rows["(orchestrator + other sessions in repo)"] = session_tokens(PROJECTS / base, since)
    return rows


def run_cost_rows(st):
    """Per-ticket tokens across every repo of the run, plus the orchestrator's own session folder."""
    rows = {}
    for r in sorted({trepo(st, t) for t in st["tasks"].values()}):
        for k, v in cost_rows(r, st["started"], {t["key"] for t in st["tasks"].values()}, other=False).items():
            rows[f"{k}@{os.path.basename(r)}"] = v
    rows["(orchestrator session)"] = session_tokens(PROJECTS / slug(st.get("root") or st["repo"]), st["started"])
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
def repo_or_none(path):
    r = sh(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=path)
    return str(pathlib.Path(r.stdout.strip()).parent) if r.returncode == 0 else None


def resolve_repo(spec, root):
    """A repo given as a path, or as a folder name under the folder the run was started from."""
    for c in (os.path.expanduser(spec), os.path.join(root, spec)):
        c = os.path.realpath(c)
        if os.path.isdir(c) and repo_or_none(c):
            return repo_or_none(c)
    sys.exit(f"repo not found: {spec!r} (looked in {root}). Clone it there or fix `repo` in tasks.json.")


def default_branch(repo):
    b = sh(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=repo).stdout.strip().replace("origin/", "")
    return b or "main"


def cmd_init(a):
    root = os.path.realpath(os.getcwd())
    tasks = json.loads(pathlib.Path(a.tasks).read_text())
    cwd_repo = repo_or_none(root)
    for t in tasks:  # every task needs a repo: its own, else the repo you are standing in
        spec = t.get("repo")
        t["_repo"] = resolve_repo(spec, root) if spec else cwd_repo
        if not t["_repo"]:
            sys.exit(f"{t['key']}: no `repo` and the current folder is not a git repo")
    # one ticket in several repos = several tasks with the same key: ids become KEY@repo
    count = {}
    for t in tasks:
        count[t["key"]] = count.get(t["key"], 0) + 1
    for t in tasks:
        multi = count[t["key"]] > 1
        t["_sub"] = re.sub(r"[^a-z0-9]+", "-", os.path.basename(t["_repo"]).lower()).strip("-") if multi else ""
        t["id"] = t.get("id") or (f"{t['key']}@{t['_sub']}" if multi else t["key"])
    ids = [t["id"] for t in tasks]
    if len(set(ids)) != len(ids):
        sys.exit("duplicate task ids: give tasks of one ticket different repos or explicit ids")
    by_key = {}
    for t in tasks:
        by_key.setdefault(t["key"], []).append(t["id"])
    for t in tasks:  # a parent may be a task id, or a ticket key meaning all its parts
        exp = []
        for p in t.get("parents", []):
            if p in ids:
                exp.append(p)
            elif p in by_key:
                exp += by_key[p]
            else:
                sys.exit(f"{t['id']}: unknown parent {p}")
        t["parents"] = exp
    ok = {"pending", "implementing", "validating", "pr_open", "in_review", "merged", "completed", "stuck"}
    for t in tasks:
        if t.get("status", "pending") not in ok:
            sys.exit(f"{t['id']}: bad status {t['status']}")
    seen, stack = set(), set()
    byid = {t["id"]: t for t in tasks}

    def visit(k):
        if k in stack:
            sys.exit(f"dependency cycle at {k}")
        if k in seen:
            return
        stack.add(k)
        for p in byid[k]["parents"]:
            visit(p)
        stack.discard(k)
        seen.add(k)
    for k in ids:
        visit(k)
    repos = sorted({t["_repo"] for t in tasks})
    bases = {r: a.base or default_branch(r) for r in repos}
    if not DRY:  # bring every repo that will start new work up to date, from origin
        for r in sorted({t["_repo"] for t in tasks if t.get("status", "pending") == "pending"}):
            if sh(["git", "status", "--porcelain"], cwd=r).stdout.strip():
                sys.exit(f"{r}: main checkout is not clean; commit or stash first")
            sh(["git", "fetch", "origin"], cwd=r)
            sh(["git", "switch", bases[r]], cwd=r)
            res = sh(["git", "pull", "--ff-only"], cwd=r)
            if res.returncode:
                sys.exit(f"{r}: git pull --ff-only failed: {res.stderr.strip()}")
    run = time.strftime("%Y%m%d-%H%M%S")
    d = STATE_ROOT / run
    d.mkdir(parents=True)
    st = {"run": run, "root": root, "bases": bases, "cap": a.cap, "started": time.time(),
          "anchor": os.environ.get("HC_TERMINAL_ID", ""), "ignore_checks": a.ignore_check or [], "tasks": {},
          "_file": str(d / "state.json")}
    for t in tasks:
        k = t["key"]
        st["tasks"][t["id"]] = {
            "id": t["id"], "key": k, "repo": t["_repo"], "sub": t["_sub"], "title": t["title"],
            "description": t.get("description", ""), "branch": t["branch"],
            "parents": t["parents"], "status": t.get("status", "pending"), "round": 0, "prs": t.get("prs", []),
            "jira": t.get("jira", "none"), "worker_name": t.get("worker_name"), "hold": t.get("hold"),
            "wait_for_merge": bool(t.get("wait_for_merge")),
            "base": t.get("base", bases[t["_repo"]]), "label": t.get("label", ""),
            "jira_enabled": bool(re.match(r"^[A-Z][A-Z0-9]+-\d+$", k)) and not k.startswith("NOJIRA"),
        }
    term = os.environ.get("HC_TERMINAL_ID")
    if term:  # bind the run to the orchestrator's terminal, so its later commands find it
        (STATE_ROOT / "by-terminal").mkdir(parents=True, exist_ok=True)
        (STATE_ROOT / "by-terminal" / term).write_text(run)
    (STATE_ROOT / "latest").write_text(run)
    if a.context:
        (d / "context.md").write_text(pathlib.Path(a.context).read_text())
    save(st)
    print(f"run {run}: {len(tasks)} tasks in {len(repos)} repo(s) ({', '.join(os.path.basename(r) for r in repos)}), cap {a.cap}")


def find_task(st, ref):
    """A task by id, or by ticket key when that key has a single task."""
    if ref in st["tasks"]:
        return st["tasks"][ref]
    hits = [t for t in st["tasks"].values() if t["key"] == ref]
    if len(hits) == 1:
        return hits[0]
    sys.exit(f"{ref}: " + ("no such task" if not hits else "several tasks, use one of: " + ", ".join(t["id"] for t in hits)))


def cmd_tick(a, loop=False):
    while True:
        st = load()
        out = tick(st)
        save(st)
        if out["jira"] or out["notify"] or out["finished"] or not loop:
            if out["finished"]:
                out["cost_hint"] = "run `orch.py cost` for the final total"
            print(json.dumps(out, indent=1))
            return
        time.sleep(a.every)


def cmd_status(a):
    st = load()
    rows = run_cost_rows(st)
    repos = sorted({trepo(st, t) for t in st["tasks"].values()})
    print(f"run {st['run']}  cap {st['cap']}  repos: {', '.join(os.path.basename(r) for r in repos)}")
    for k, t in st["tasks"].items():
        tok = rows.get(f"{t['key']}@{os.path.basename(trepo(st, t))}", {})
        n = sum(x["output"] + x["input"] + x["cache_write"] for x in tok.values())
        print(f"{k:22} {t['status']:12} round {t['round']}/{MAX_ROUNDS}  PRs {t['prs'] or '-'}  jira {t['jira']:11} "
              f"parents {','.join(t['parents']) or '-'}  worker {wname(t)}  ~{n/1000:,.0f}k new tokens")


def cmd_set_jira(a):
    st = load()
    hits = [t for t in st["tasks"].values() if t["key"] == a.key or t["id"] == a.key]
    if not hits:
        sys.exit(f"{a.key}: no such ticket or task")
    for t in hits:  # a ticket spanning several repos has one Jira status
        if a.to.startswith("failed:"):
            t.setdefault("jira_failed", {})[a.to.split(":", 1)[1]] = True
        else:
            t["jira"] = a.to
    save(st)


def cmd_retry(a):
    st = load()
    t = find_task(st, a.key)
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
    """Launch an orchestrator agent in its own tab, from any folder: it works out the repos from the tickets.
    `continue` messages an orchestrator already running in this folder instead."""
    root = os.path.realpath(os.getcwd())
    request = a.request or "continue"
    live = hc_live()
    if request.strip().lower().startswith("continue"):
        for n, t in live.items():
            if n.startswith("orchestrator") and os.path.realpath(t.get("cwd", "")) == root:
                hc_send(n, request)
                print(f"orchestrator already running in terminal {n}; sent it your message")
                return
    name, i = "orchestrator", 2
    while name in live:
        name, i = f"orchestrator-{i}", i + 1
    argv = ["claude", "--agent", "orchestrator", "--permission-mode", "auto", "--add-dir", root,
            "--allowedTools", ORCH_ALLOW, "--disallowedTools", ORCH_DENY]
    hc_start({"anchor": os.environ.get("HC_TERMINAL_ID", "")}, name, root, argv, "orchestrator")
    hc_label(name, role="orchestrator")
    hc_send(name, request)
    print(f"orchestrator started in a new tab ({name}) from {root}; continue there")


def cmd_repos(a):
    """List the git repos directly under a folder, so the orchestrator can match names it finds in tickets."""
    root = pathlib.Path(os.path.realpath(a.root or os.getcwd()))
    cands = [root] if (root / ".git").exists() else []
    cands += sorted(d for d in root.iterdir() if d.is_dir() and (d / ".git").exists())
    out = []
    for d in cands:
        url = sh(["git", "-C", str(d), "remote", "get-url", "origin"]).stdout.strip()
        out.append({"name": d.name, "path": str(d), "remote": url})
    print(json.dumps(out, indent=1))


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
        o = {"key": k, "repo": repo, "title": t.get("title") or "", "description": t.get("description") or "",
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
    st = load()
    find_task(st, a.key)["hold"] = None if a.release else (a.reason or "on hold")
    save(st)
    print(("released " if a.release else "held ") + a.key)


def cmd_cost(a):
    if a.repo:  # any repo's worktree sessions, no run needed
        print_cost(cost_rows(os.path.realpath(a.repo), a.since or 0))
        return
    st = load()
    st["started"] = a.since or st["started"]
    print_cost(run_cost_rows(st))


def main():
    global RUN_ARG
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", help="run id (default: the run bound to this terminal)")
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
    p = sp.add_parser("repos"); p.add_argument("root", nargs="?"); p.set_defaults(f=cmd_repos)
    p = sp.add_parser("cost"); p.add_argument("--repo"); p.add_argument("--since", type=float); p.set_defaults(f=cmd_cost)
    a = ap.parse_args()
    RUN_ARG = a.run
    a.f(a)


if __name__ == "__main__":
    main()
