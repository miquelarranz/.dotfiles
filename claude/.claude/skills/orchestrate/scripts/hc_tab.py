#!/usr/bin/env python3
"""Add or remove a tab in the Herdr desktop app layout (herdr-core `herdr-app` doc).

Usage:
  hc_tab.py add <terminal_id> <tab_name> [--space-of <terminal_id>]
  hc_tab.py remove <terminal_id>

`add` puts a new tab holding <terminal_id> into the Space whose layout already
contains --space-of (default: $HC_TERMINAL_ID, i.e. the orchestrator's own terminal).
Prints JSON {"tab_id": ...}. Writes are version-checked (doc.set) and retried on conflict.
"""
import json
import os
import subprocess
import sys
import uuid

DOC = "herdr-app"


def rpc(method, params):
    req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    out = subprocess.run(["hc", "connect"], input=req + "\n", capture_output=True, text=True, timeout=15).stdout
    line = next((l for l in out.splitlines() if l.strip().startswith("{")), "")
    resp = json.loads(line)
    if "error" in resp:
        raise RuntimeError(resp["error"])
    return resp["result"]


def terminals_in(node):
    if isinstance(node, dict):
        content = node.get("content")
        if isinstance(content, dict) and "terminal" in content:
            yield content["terminal"]
        for v in node.values():
            yield from terminals_in(v)
    elif isinstance(node, list):
        for v in node:
            yield from terminals_in(v)


def mutate(fn):
    for _ in range(5):
        cur = rpc("doc.get", {"name": DOC})
        value, version = cur["value"], cur["version"]
        result = fn(value)
        try:
            rpc("doc.set", {"name": DOC, "value": value, "version": version})
            return result
        except RuntimeError as e:
            if "conflict" not in str(e):
                raise
    raise SystemExit("doc.set kept conflicting; layout changed too often")


def add(terminal, name, anchor):
    def fn(value):
        space = next((s for s in value["spaces"] if anchor in set(terminals_in(s))), None)
        if space is None:
            raise SystemExit(f"no Space contains terminal {anchor}; pass --space-of")
        tab_id = "tb" + uuid.uuid4().hex
        space["tabs"].append({
            "id": tab_id,
            "name": name,
            "root": {
                "id": tab_id,
                "type": "row",
                "children": [{"id": "p" + uuid.uuid4().hex, "type": "block", "content": {"terminal": terminal}}],
            },
        })
        return {"tab_id": tab_id, "space": space["name"]}

    return mutate(fn)


def remove(terminal):
    def fn(value):
        removed = []
        for s in value["spaces"]:
            keep = []
            for t in s["tabs"]:
                if list(terminals_in(t)) == [terminal]:
                    removed.append(t["id"])
                else:
                    keep.append(t)
            s["tabs"] = keep
        return {"removed": removed}

    return mutate(fn)


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) >= 3 and a[0] == "add":
        anchor = os.environ.get("HC_TERMINAL_ID", "")
        if "--space-of" in a:
            anchor = a[a.index("--space-of") + 1]
        print(json.dumps(add(a[1], a[2], anchor)))
    elif len(a) == 2 and a[0] == "remove":
        print(json.dumps(remove(a[1])))
    else:
        sys.exit(__doc__)
