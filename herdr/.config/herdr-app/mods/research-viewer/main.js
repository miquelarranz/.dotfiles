// Research viewer: renders the vault note of the focused Claude session in the right panel.
// Hooks (~/.claude/hooks) register every vault note a Claude session writes, per session (see registry.js).
// This mod follows the focused terminal's latest note, re-reads it whenever anything under Research/
// changes, and has an "all notes" tree of the vault folders. Revealing the panel is done by the hooks.
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex, TextView } from "gpui-base";
import { home, exec, readTextFile, watchFolder, focused, theme } from "herdr";
import { Registry, REGS } from "./registry.js";

const ROOT = () => `${home()}/Desktop/Vault/Research`;

const sh = async (script, ...args) => {
  const r = await exec("sh", ["-c", script, "rv", ...args]);
  return r.code === 0 ? r.stdout : "";
};

// "---\na: b\n---\nbody" -> { meta: {a: "b"}, body }
function split(text) {
  const m = /^---\n([\s\S]*?)\n---\n?/.exec(text);
  if (!m) return { meta: {}, body: text };
  const meta = {};
  for (const line of m[1].split("\n")) {
    const i = line.indexOf(":");
    if (i > 0) meta[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  return { meta, body: text.slice(m[0].length) };
}

// Obsidian [[Note|alias]] -> plain text; TextView cannot resolve wikilinks.
const plain = (s) => s.replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, "$2").replace(/\[\[([^\]]+)\]\]/g, "$1");

const title = (path) => path.split("/").pop().replace(/\.md$/, "").replace(/^\d{4}-\d{2}-\d{2} /, "");
const area = (path) => path.split("/").slice(-2, -1)[0];

export default class ResearchViewer extends View {
  init(_props, cx) {
    this.reg = new Registry();
    this.tree = [];          // [{ name, notes: [path] }]: folders A-Z, notes newest first
    this.open = new Set(JSON.parse(localStorage.getItem("open") ?? "[]"));
    this.sessionClosed = false;
    this.sel = null;         // { key, path }: the session tab you clicked; cleared when the session gets a new note
    this.known = new Map();  // focus key -> Set of note paths already seen
    this.pinned = null;      // a note picked by hand; null follows the focused session
    this.path = null;
    this.text = null;
    this.error = null;
    this.picker = false;
    this.reloadAll(cx);
    cx.timer.every(1000, (cx) => this.follow(cx));
    cx.spawn(async (cx) => {
      for (;;) {
        try { await watchFolder(ROOT()); } catch { await new Promise((r) => cx.timer.after?.(5000, r)); }
        await this.reloadAll(cx);
      }
    });
    cx.spawn(async (cx) => {
      for (;;) {
        try { await watchFolder(REGS()); } catch { return; }
        await this.reloadAll(cx);
      }
    });
  }

  async reloadAll(cx) {
    try {
      await this.reg.reload();
      const files = (await sh('find "$1" -name "*.md" -not -path "*/_files/*" -not -path "*/.*"', ROOT())).split("\n").filter(Boolean);
      const groups = new Map();
      for (const f of files) {
        const rel = f.slice(ROOT().length + 1), name = rel.includes("/") ? rel.split("/")[0] : "(no folder)";
        if (!groups.has(name)) groups.set(name, []);
        groups.get(name).push(f);
      }
      this.tree = [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0]))
        .map(([name, notes]) => ({ name, notes: notes.sort((a, b) => b.split("/").pop().localeCompare(a.split("/").pop())) }));
    } catch (e) { this.error = String(e); }
    await this.load(this.want(), true, cx);
  }

  // The note this copy should show right now.
  // Which session the focus is on: a terminal, or its tab for panes that are not agents.
  focusKey() { const f = focused(); return f ? (f.terminal ?? f.tab) : null; }

  want() {
    if (this.pinned) return this.pinned;
    const f = focused();
    if (!f) return this.path;
    const key = this.focusKey();
    const paths = this.sessionNotes(f).map((n) => n.path);
    // A note this session did not have before: jump to it (the panel was just revealed for it).
    const known = this.known.get(key);
    if (known && paths.some((p) => !known.has(p))) this.sel = null;
    this.known.set(key, new Set(paths));
    if (this.sel?.key === key && paths.includes(this.sel.path)) return this.sel.path;
    return paths[0] ?? null;
  }

  select(path, cx) {
    this.pinned = null; this.picker = false;
    this.sel = { key: this.focusKey(), path };
    this.load(path, false, cx); cx.notify();
  }

  sessionNotes(f = focused()) { return this.reg.notes(f); }

  follow(cx) {
    const want = this.want();
    if (want !== this.path) this.load(want, false, cx);
  }

  async load(path, force, cx) {
    if (!path) {
      if (this.path !== null) { this.path = null; this.text = null; cx.notify(); }
      return;
    }
    if (path === this.path && !force) return;
    let text = null, error = null;
    try { text = await readTextFile(path); if (text === null) error = "This note no longer exists"; }
    catch (e) { error = String(e); }
    if (path === this.path && text === this.text && error === this.error) return;
    this.path = path; this.text = text; this.error = error;
    cx.notify();
  }

  toggle(name, cx) {
    this.open.has(name) ? this.open.delete(name) : this.open.add(name);
    try { localStorage.setItem("open", JSON.stringify([...this.open])); } catch {}
    cx.notify();
  }

  pick(path, cx) { this.pinned = path; this.picker = false; this.load(path, false, cx); cx.notify(); }
  follow_session(cx) { this.pinned = null; this.picker = false; this.load(this.want(), false, cx); cx.notify(); }

  render(cx) {
    const t = theme();
    const root = v_flex().id("rv-root").size_full().bg(t.surface);

    const bar = h_flex().id("rv-bar").flex_none().px(10).py(6).gap(8).items_center().border_b(1).border_color(t.border)
      .child(div().flex_1().truncate().text_size(13).font_semibold().text_color(t.foreground)
        .child(this.path ? title(this.path) : "Research notes"))
      .child(div().id("rv-pin").px(6).py(2).rounded(4).text_size(11)
        .text_color(this.pinned ? t.accent : t.dim).hover((s) => s.bg(t.raised))
        .on_click((_e, cx) => this.pinned ? this.follow_session(cx) : (this.path && this.pick(this.path, cx)))
        .child(this.pinned ? "pinned" : "following"))
      .child(div().id("rv-recent").px(6).py(2).rounded(4).text_size(11).text_color(t.muted)
        .hover((s) => s.bg(t.raised)).on_click((_e, cx) => {
          this.picker = !this.picker;
          if (this.picker && this.path) this.open.add(area(this.path));   // open on the folder of the note you are reading
          cx.notify();
        })
        .child("all notes"));

    if (this.picker) {
      const date = (p) => (/^(\d{4}-\d{2}-\d{2}) /.exec(p.split("/").pop()) ?? [])[1] ?? "";
      const note = (p, key, indent) => h_flex().id(key).pl(indent).pr(10).py(5).gap(6).items_center()
        .bg(p === this.path ? t.selected : t.surface).hover((s) => s.bg(t.hover)).on_click((_e, cx) => this.pick(p, cx))
        .child(svg("mods/research-viewer/file.svg").size(12).text_color(t.dim))
        .child(div().flex_1().min_w(0).truncate().text_size(12).text_color(p === this.path ? t.foreground : t.muted).child(title(p)))
        .child(div().flex_none().text_size(10).text_color(t.dim).child(date(p)));
      const folder = (name, count, open, key, onClick) => h_flex().id(key).px(10).py(5).gap(6).items_center()
        .hover((s) => s.bg(t.hover)).on_click(onClick)
        .child(svg(open ? "mods/research-viewer/chevron-down.svg" : "mods/research-viewer/chevron-right.svg").size(12).text_color(t.dim))
        .child(svg("mods/research-viewer/folder.svg").size(13).text_color(t.dim))
        .child(div().flex_1().truncate().text_size(12).font_semibold().text_color(t.foreground).child(name))
        .child(div().text_size(10).text_color(t.dim).child(String(count)));
      const mine = this.sessionNotes().map((n) => n.path);
      const rows = [];
      if (mine.length) {
        const o = !this.sessionClosed;
        rows.push(folder("This session", mine.length, o, "rv-f-session", (_e, cx) => { this.sessionClosed = !this.sessionClosed; cx.notify(); }));
        if (o) mine.forEach((p, i) => rows.push(note(p, `rv-s${i}`, 28)));
      }
      this.tree.forEach((g, gi) => {
        const o = this.open.has(g.name);
        rows.push(folder(g.name, g.notes.length, o, `rv-f${gi}`, (_e, cx) => this.toggle(g.name, cx)));
        if (o) g.notes.forEach((p, i) => rows.push(note(p, `rv-n${gi}-${i}`, 28)));
      });
      return root.child(bar).child(div().id("rv-tree").flex_1().overflow_y_scrollbar().py(4).children(rows));
    }

    if (this.error || this.text === null) {
      return root.child(bar).child(div().p(14).text_size(12).text_color(t.dim)
        .child(this.error ?? "No note for this session yet. Run /investigate, or pick one from “all notes”."));
    }

    const { meta, body } = split(this.text);
    const mine = this.sessionNotes().map((n) => n.path).sort((a, b) => a.split("/").pop().localeCompare(b.split("/").pop()));
    const tabs = mine.length > 1 ? h_flex().id("rv-tabs").flex_none().h(32).px(6).gap(2).items_end().border_b(1).border_color(t.border).overflow_x_scrollbar().children(
      mine.map((p, i) => div().id(`rv-t${i}`).flex_none().max_w(150).px(8).py(6).truncate().text_size(11)
        .text_color(p === this.path ? t.foreground : t.muted)
        .border_b(2).border_color(p === this.path ? t.accent : t.surface)
        .hover((s) => s.bg(t.hover)).on_click((_e, cx) => this.select(p, cx)).child(title(p)))) : null;
    const chips = ["area", "type", "status", "updated"].filter((k) => meta[k]).map((k) => meta[k]);
    return root.child(bar)
      .when(tabs !== null, (el) => el.child(tabs))
      .when(chips.length > 0, (el) => el.child(
        h_flex().id("rv-meta").flex_none().px(10).py(5).gap(6).border_b(1).border_color(t.border).children(
          chips.map((c, i) => div().id(`rv-c${i}`).px(6).py(1).rounded(4).bg(t.raised).text_size(10).text_color(t.muted).child(c)))))
      .child(div().id("rv-body").flex_1().min_h(0).overflow_y_scrollbar().px(12).py(10)
        .child(TextView.markdown("rv-md", plain(body)).selectable(true)));
  }
}
