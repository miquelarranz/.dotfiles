// COPY of ../research-viewer/registry.js (mods cannot import across folders). Keep the two identical.
// Which vault notes belong to which Herdr terminal. Shared by the Research panel and the tab strip.
// Written by ~/.claude/hooks (open-in-herdr.sh, link-session-herdr.sh):
//   data/research-viewer/sessions/<claude session>.json   { notes: [{ path, seq }] }
//   data/research-viewer/terminals/<terminal>.json        { sessions: [session id, newest first] }
import { appHome, exec, readTextFile, watchFolder, focused, agents } from "herdr";

export const REGS = () => `${appHome()}/data/research-viewer`;

export class Registry {
  constructor() { this.sessions = new Map(); this.terms = new Map(); }

  async reload() {
    const list = async (dir) => (await exec("sh", ["-c", 'for f in "$1"/*.json; do [ -e "$f" ] && printf "%s\\n" "$f"; done', "rv", dir]))
      .stdout.split("\n").filter(Boolean);
    const read = async (dir, pick) => {
      const out = new Map();
      for (const f of await list(dir)) {
        try {
          const v = pick(JSON.parse((await readTextFile(f)) ?? "{}"));
          if (v?.length) out.set(f.split("/").pop().replace(/\.json$/, ""), v);
        } catch {}
      }
      return out;
    };
    this.sessions = await read(`${REGS()}/sessions`, (j) => j.notes);
    this.terms = await read(`${REGS()}/terminals`, (j) => j.sessions);
  }

  // Reload now, then again whenever anything under the registry changes. `done(cx)` runs after each load.
  watch(cx, done) {
    cx.spawn(async (cx) => {
      await this.reload().catch(() => {}); done(cx);
      for (;;) {
        try { await watchFolder(REGS()); } catch { return; }
        await this.reload().catch(() => {}); done(cx);
      }
    });
  }

  // Notes written by the Claude sessions that ran in the focused terminal (so they survive /clear and
  // --resume), newest first. Falls back to the other terminals in the same tab, then the same space,
  // so a shell pane beside Claude still shows Claude's notes.
  notes(f = focused()) {
    if (!f) return [];
    const of = (terms) => {
      const best = new Map();
      for (const term of terms) for (const sid of this.terms.get(term) ?? []) for (const n of this.sessions.get(sid) ?? [])
        if (!best.has(n.path) || best.get(n.path).seq < n.seq) best.set(n.path, n);
      return [...best.values()].sort((a, b) => b.seq - a.seq);
    };
    const all = agents();
    const own = f.terminal ? of([f.terminal]) : [];
    if (own.length) return own;
    const tab = of(all.filter((a) => a.tab === f.tab).map((a) => a.address));
    if (tab.length) return tab;
    return of(all.filter((a) => a.space === f.space).map((a) => a.address));
  }
}
