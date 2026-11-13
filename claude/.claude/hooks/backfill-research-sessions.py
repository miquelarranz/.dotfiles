#!/usr/bin/env python3
"""One-off/idempotent: register vault notes written by past Claude sessions with the Herdr
research-viewer, by scanning session transcripts for Write/Edit calls on Research/*.md.
Writes <herdr-app>/data/research-viewer/sessions/<session id>.json. Never overwrites newer data."""
import glob, json, os, sys, datetime
home = os.path.expanduser("~")
vault = f"{home}/Desktop/Vault/Research/"
out = f"{os.environ.get('HERDR_APP_HOME', home + '/.config/herdr-app')}/data/research-viewer/sessions"
os.makedirs(out, exist_ok=True)
found = {}
# Notes were moved into area folders after some sessions wrote them: resolve old paths by file name.
index = {os.path.basename(p): p for p in glob.glob(vault + "*/*.md")}
def current(fp):
    if os.path.exists(fp):
        return fp
    return index.get(os.path.basename(fp))
for path in glob.glob(f"{home}/.claude/projects/*/*.jsonl"):
    sid = os.path.basename(path)[:-6]
    notes = {}
    try:
        for line in open(path, errors="ignore"):
            if vault not in line or '"file_path"' not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            content = e.get("message", {}).get("content")
            if not isinstance(content, list):
                continue
            for c in content:
                fp = (c.get("input") or {}).get("file_path", "") if c.get("type") == "tool_use" else ""
                fp = current(fp) if fp.startswith(vault) and fp.endswith(".md") else ""
                if fp:
                    ts = e.get("timestamp", "")
                    try:
                        seq = int(datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
                    except ValueError:
                        seq = int(os.path.getmtime(path))
                    notes[fp] = max(seq, notes.get(fp, 0))
    except OSError:
        continue
    if notes:
        found[sid] = sorted(({"path": p, "seq": s} for p, s in notes.items()), key=lambda n: n["seq"])
written = 0
for sid, notes in found.items():
    f = f"{out}/{sid}.json"
    if os.path.exists(f):
        have = {n["path"]: n for n in json.load(open(f)).get("notes", [])}
        for n in notes:
            if n["path"] not in have or have[n["path"]]["seq"] < n["seq"]:
                have[n["path"]] = n
        notes = sorted(have.values(), key=lambda n: n["seq"])
    json.dump({"notes": notes}, open(f, "w"))
    written += 1
    print(sid, len(notes), "notes")
print(f"{written} sessions backfilled")
