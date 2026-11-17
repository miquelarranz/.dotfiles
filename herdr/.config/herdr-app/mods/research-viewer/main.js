// Research viewer: renders the vault note of the focused Claude session in the right panel.
// Hooks (~/.claude/hooks) register every vault note a Claude session writes, per session (see registry.js).
// This mod follows the focused terminal's latest note, re-reads it whenever anything under Research/
// changes, and has an "all notes" tree of the vault folders. Revealing the panel is done by the hooks.
import { View, div, svg, image } from "gpui-kit";
import { v_flex, h_flex, TextView, Editor, EditorState } from "gpui-base";
import { home, exec, readTextFile, writeTextFile, watchFolder, focused, theme } from "herdr";
import { Registry, REGS } from "./registry.js";
import { readFile, writeFile, mkdir, exists } from "fs/promises";
import { createHash } from "crypto";

// PlantUML: ```plantuml fences are rendered to PNG with the local `plantuml` CLI (brew install plantuml; graphviz for
// non-sequence diagrams), cached by content hash under herdr-app/cache/plantuml. Nothing is sent to a remote server.
const UML_DIR = () => `${home()}/.config/herdr-app/cache/plantuml`;
const UML_REL = "cache/plantuml";   // image() paths resolve against the herdr-app root
const UML_FENCE = /^```(?:plantuml|puml)[^\n]*\n([\s\S]*?)^```[ \t]*$/gm;
const UML_DPI = 144;                // rendered at 1.5x of the 96 dpi default, shown at logical size
const UML_MAX_W = 640;

// Body -> [{ md } | { uml }] in document order.
function segments(body) {
  const out = [];
  let last = 0, n = 0;
  for (const m of body.matchAll(UML_FENCE)) {
    if (m.index > last) out.push({ md: body.slice(last, m.index), at: last });
    out.push({ uml: m[1], n: n++, at: m.index });
    last = m.index + m[0].length;
  }
  if (last < body.length) out.push({ md: body.slice(last), at: last });
  return out;
}

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

const isDiagram = (path) => /\.(puml|plantuml)$/.test(path);
const title = (path) => path.split("/").pop().replace(/\.(md|puml|plantuml)$/, "").replace(/^\d{4}-\d{2}-\d{2} /, "");
const area = (path) => path.slice(ROOT().length + 1).split("/")[0];
// "<note title> - <diagram name>.puml" in <folder>/_files belongs to the notes of <folder> whose title starts with
// (or is a prefix of) <note title>; the date is ignored since a diagram can be written on a later day than its note.
const dirOf = (p) => p.split("/").slice(0, -1).join("/");
const relatedTo = (diagram, notePaths) => {
  const t = title(diagram).split(" - ")[0];
  const folder = dirOf(dirOf(diagram));
  return notePaths.some((n) => dirOf(n) === folder && (title(n).startsWith(t) || t.startsWith(title(n))));
};

// What a selection of the rendered text was in the markdown source. The host copies the *rendered* text (no
// `**`, `#`, `- `, link urls); this maps that text back onto the source and returns the markdown slice, so a copy
// from the rendered view pastes as markdown, like Notion. Returns null when the text is not found (leave the clipboard).
function projection(src) {
  let P = "";
  const map = [];                       // map[i] = offset in src of P[i]
  const put = (ch, at) => {
    if (/\s/.test(ch)) { if (P !== "" && !P.endsWith(" ")) { P += " "; map.push(at); } return; }
    P += ch; map.push(at);
  };
  let off = 0, fence = false;
  for (const line of src.split(/(?<=\n)/)) {
    const base = off; off += line.length;
    if (/^\s*(```|~~~)/.test(line)) { fence = !fence; put("\n", base); continue; }
    let i = 0;
    if (!fence) {
      const pre = /^(\s*(?:#{1,6}\s+|>\s?|[-*+]\s+(?:\[[ xX]\]\s+)?|\d+[.)]\s+))+/.exec(line);
      if (pre) i = pre[0].length;
      if (/^\s*\|?[\s:|-]+\|?\s*$/.test(line) && line.includes("-") && line.includes("|")) { put("\n", base); continue; }
    }
    for (; i < line.length; i++) {
      const c = line[i], rest = line.slice(i);
      if (!fence) {
        if (/^(\*\*|__|~~)/.test(rest)) { i++; continue; }
        if (c === "`" || c === "*") continue;
        if (c === "|") { put(" ", base + i); continue; }
        if (c === "[") {
          const wiki = /^\[\[([^\]|]+\|)?([^\]]+)\]\]/.exec(rest);
          if (wiki) { const pre2 = wiki[1]?.length ?? 0, t = wiki[2]; for (let k = 0; k < t.length; k++) put(t[k], base + i + 2 + pre2 + k); i += wiki[0].length - 1; continue; }
          const link = /^\[([^\]]*)\]\(([^)]*)\)/.exec(rest);
          if (link) { for (let k = 0; k < link[1].length; k++) put(link[1][k], base + i + 1 + k); i += link[0].length - 1; continue; }
          continue;
        }
      }
      put(c, base + i);
    }
    put("\n", base + line.length - 1);
  }
  return { P, map };
}

function selectionToMarkdown(src, selected) {
  const norm = (t) => t.replace(/\s+/g, " ").trim();
  const { P, map } = projection(src);
  const tries = [norm(selected),
    norm(selected.split("\n").map((l) => l.replace(/^\s*(?:[•◦▪·]\s*|[-*+]\s+|\d+[.)]\s+)/, "")).join("\n"))];
  for (const sel of tries) {
    if (!sel) continue;
    const at = P.indexOf(sel);
    if (at < 0) continue;
    let a = map[at], b = map[at + sel.length - 1] + 1;
    const o = src.lastIndexOf("[[", a);                                // started inside a [[wikilink]]: take all of it
    if (o >= 0 && o > src.lastIndexOf("]]", a - 1)) a = o;
    const c = src.lastIndexOf("[[", b - 1);
    if (c >= 0 && c > src.lastIndexOf("]]", b - 1) && src.indexOf("]]", b) >= 0) b = src.indexOf("]]", b) + 2;
    while (a > 0 && /[*_~`\[]/.test(src[a - 1])) a--;                  // opening ** _ ` [ right before
    while (b < src.length && /[*_~`]/.test(src[b])) b++;               // closing marks right after
    if (src.startsWith("](", b)) { const e = src.indexOf(")", b); if (e > 0) b = e + 1; }
    const ls = src.lastIndexOf("\n", a - 1) + 1;
    if (/^\s*(#{1,6}\s+|>\s?|[-*+]\s+(\[[ xX]\]\s+)?|\d+[.)]\s+)*$/.test(src.slice(ls, a))) a = ls;   // keep the line's marker
    // A selection inside a markdown table: take whole rows, and add the header and separator rows when the
    // selection starts below them, so it pastes as a table (Notion turns a markdown table into a table block).
    const isRow = (l) => /^\s*\|/.test(l);
    const lineAt = (o) => { const st = src.lastIndexOf("\n", o - 1) + 1; let en = src.indexOf("\n", o); if (en < 0) en = src.length; return [st, en]; };
    let [s0] = lineAt(a), [, e1] = lineAt(Math.max(b - 1, 0));
    if (isRow(src.slice(s0, lineAt(a)[1])) && isRow(src.slice(lineAt(Math.max(b - 1, 0))[0], e1))) {
      let top = s0;
      for (;;) { if (top === 0) break; const [ps] = lineAt(top - 1); if (!isRow(src.slice(ps, top - 1))) break; top = ps; }
      const out = src.slice(s0, e1);
      if (top === s0) return out;
      const [, h1] = lineAt(top), [s2, h2] = lineAt(h1 + 1);
      return src.slice(top, h1) + "\n" + src.slice(s2, h2) + "\n" + out;
    }
    return src.slice(a, b).replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, "$2").replace(/\[\[([^\]]+)\]\]/g, "$1");
  }
  return null;
}

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
    this.diagrams = new Map(); // content hash -> { state: "pending" | "ok" | "error", w, h, error, ready: Promise }
    this.editors = new Map();  // "<note path>#<n>" -> { state: EditorState, saved, live, lastOk, token } for diagrams opened for editing
    this.lastClip = undefined;  // the clipboard text last seen (ours or anyone's), so each copy is handled once
    this.flash = null;         // { key, msg }: short feedback next to a diagram's buttons
    this.reloadAll(cx);
    cx.timer.every(1000, (cx) => this.follow(cx));
    cx.timer.every(300, (cx) => this.watchClipboard(cx));
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
      const files = (await sh('find "$1" \\( \\( -name "*.md" -not -path "*/_files/*" \\) -o \\( \\( -name "*.puml" -o -name "*.plantuml" \\) -path "*/_files/*" \\) \\) -not -path "*/.*"', ROOT())).split("\n").filter(Boolean);
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
    try {
      text = await readTextFile(path);
      if (text === null) error = "This note no longer exists";
      else if (isDiagram(path)) text = "```plantuml\n" + text.replace(/\n*$/, "\n") + "```\n";
    }
    catch (e) { error = String(e); }
    if (path === this.path && text === this.text && error === this.error) return;
    this.path = path; this.text = text; this.error = error;
    if (text !== null) {   // an editor with no unsaved edits follows the file
      const blocks = segments(split(text).body).filter((g) => g.uml !== undefined);
      for (const g of blocks) {
        const ed = this.editors.get(`${path}#${g.n}`);
        if (ed && ed.state.value() === ed.saved && g.uml !== ed.saved) { ed.saved = g.uml; ed.live = g.uml; ed.state.set_value(g.uml); }
      }
    }
    cx.notify();
  }

  // Kicks off (once) the render of one diagram; the view repaints when it finishes.
  diagram(src, cx) {
    const id = createHash("sha1").update(src).digest("hex").slice(0, 16);
    let d = this.diagrams.get(id);
    if (d) return { id, ...d };
    let done;
    const ready = new Promise((r) => { done = r; });
    d = { state: "pending", ready };
    this.diagrams.set(id, d);
    cx.spawn(async (cx) => {
      try {
        const dir = UML_DIR(), png = `${dir}/${id}.png`;
        await mkdir(dir, { recursive: true });
        if (!(await exists(png))) {
          const body = /@start/.test(src) ? src : `@startuml\n${src}\n@enduml\n`;
          await writeFile(`${dir}/${id}.puml`, body);
          const r = await exec("plantuml", ["-tpng", `-Sdpi=${UML_DPI}`, "-nometadata", `${dir}/${id}.puml`], { timeout: 60000 });
          if (r.code !== 0 || !(await exists(png))) throw new Error((r.stderr || r.stdout || "plantuml failed").trim().slice(0, 300));
        }
        const b = await readFile(png);   // PNG IHDR: width and height are big-endian u32 at bytes 16 and 20
        const u32 = (o) => ((b[o] << 24) | (b[o + 1] << 16) | (b[o + 2] << 8) | b[o + 3]) >>> 0;
        const k = 96 / UML_DPI, w = u32(16) * k, h = u32(20) * k, f = Math.min(1, UML_MAX_W / w);
        this.diagrams.set(id, { state: "ok", ready, w: Math.round(w * f), h: Math.round(h * f) });
      } catch (e) {
        this.diagrams.set(id, { state: "error", ready, error: String(e?.message ?? e) });
      }
      done();
      cx.notify();
    });
    return { id, ...d };
  }

  // Opens or closes the source editor under a diagram. The preview follows what is typed (debounced).
  toggleEditor(key, src, n, cx) {
    const cur = this.editors.get(key);
    if (cur) { cur.state.release(); this.editors.delete(key); cx.notify(); return; }
    const state = EditorState.new({ value: src, line_numbers: false, soft_wrap: true });
    const ed = { state, saved: src, live: src, lastOk: null, token: 0, key, n, path: this.path };
    state.on("change", (_e, cx) => {
      const tok = ++ed.token;
      cx.timer.after(350, (acx) => {
        if (tok !== ed.token) return;
        ed.live = state.value(); acx.notify();
        this.save(ed, acx);   // autosave once typing pauses
      });
      cx.notify();   // the "unsaved" marker
    });
    this.editors.set(key, ed);
    cx.notify();
  }

  // The host copies the *rendered* text of a selection, and the note view never hears Cmd+C, so watch the
  // clipboard instead: when new text appears that is a passage of the open note, swap it for the same passage as
  // markdown (see selectionToMarkdown). Text that is not in the note is left alone.
  watchClipboard(cx) {
    const sel = cx.read_from_clipboard();
    if (sel === this.lastClip) return;
    const first = this.lastClip === undefined;
    this.lastClip = sel;
    if (first || !sel || sel.length < 3 || this.text === null) return;
    const md = selectionToMarkdown(split(this.text).body, sel);
    if (md && md !== sel) { this.lastClip = md; cx.write_to_clipboard(md); }
  }

  say(key, msg, cx) {
    this.flash = { key, msg };
    cx.notify();
    cx.timer.after(2000, (acx) => { if (this.flash?.msg === msg) { this.flash = null; acx.notify(); } });
  }

  // Puts the rendered PNG on the system clipboard as an image (paste it into Notion, Slack, ...).
  copyImage(key, src, cx) {
    const { id, ready } = this.diagram(src, cx);
    cx.spawn(async (acx) => {   // a handler's cx is not valid after an await, the spawned one is
      await ready;
      if (this.diagrams.get(id).state !== "ok") return this.say(key, "nothing to copy: fix the diagram first", acx);
      const r = await exec("osascript", ["-e", "on run argv", "-e", "set the clipboard to (read (POSIX file (item 1 of argv)) as «class PNGf»)", "-e", "end run", `${UML_DIR()}/${id}.png`]);
      this.say(key, r.code === 0 ? "image copied" : `copy failed: ${r.stderr.trim().slice(0, 80)}`, acx);
    });
  }

  // Writes the editor's text back into its file: the n-th ```plantuml block, or the whole .puml file.
  // Runs on its own after typing pauses; `acx` must be a context that is still valid (a timer's or spawn's).
  async save(ed, acx) {
    const value = ed.state.value();
    if (ed.saving || value === ed.saved) return;
    ed.saving = true;
    const before = ed.saved;
    try {
      const cur = await readTextFile(ed.path);
      if (cur === null) throw new Error("the file no longer exists");
      const code = value.replace(/\n*$/, "\n");
      let next;
      if (isDiagram(ed.path)) next = code;
      else {
        const m = [...cur.matchAll(UML_FENCE)][ed.n];
        if (!m) throw new Error("diagram block not found in the file");
        const at = m.index + m[0].indexOf("\n") + 1;
        next = cur.slice(0, at) + code + cur.slice(at + m[1].length);
      }
      ed.saved = value;   // before the write, so the file watcher's reload sees it as already ours
      try { await writeTextFile(ed.path, next, cur); } catch (e) { ed.saved = before; throw e; }
      this.say(ed.key, "saved", acx);
    } catch (e) { this.say(ed.key, `save failed: ${String(e?.message ?? e).slice(0, 80)}`, acx); }
    finally {
      ed.saving = false;
      if (ed.state.value() !== ed.saved) this.save(ed, acx);   // typed more while writing
    }
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
        .child(svg(isDiagram(p) ? "mods/research-viewer/note.svg" : "mods/research-viewer/file.svg").size(12).text_color(isDiagram(p) ? t.accent : t.dim))
        .child(div().flex_1().min_w(0).truncate().text_size(12).text_color(p === this.path ? t.foreground : t.muted).child(title(p)))
        .child(div().flex_none().text_size(10).text_color(t.dim).child(date(p)));
      const folder = (name, count, open, key, onClick) => h_flex().id(key).px(10).py(5).gap(6).items_center()
        .hover((s) => s.bg(t.hover)).on_click(onClick)
        .child(svg(open ? "mods/research-viewer/chevron-down.svg" : "mods/research-viewer/chevron-right.svg").size(12).text_color(t.dim))
        .child(svg("mods/research-viewer/folder.svg").size(13).text_color(t.dim))
        .child(div().flex_1().truncate().text_size(12).font_semibold().text_color(t.foreground).child(name))
        .child(div().text_size(10).text_color(t.dim).child(String(count)));
      const sessionNotes = this.sessionNotes().map((n) => n.path);
      const diagrams = this.tree.flatMap((g) => g.notes).filter((p) => isDiagram(p) && relatedTo(p, sessionNotes))
        .sort((a, b) => b.split("/").pop().localeCompare(a.split("/").pop()));
      const mine = [...sessionNotes, ...diagrams];
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
        .children(segments(body).map((seg, i) => {
          if (seg.md !== undefined) return TextView.markdown(`rv-md-${i}`, plain(seg.md)).selectable(true);
          const key = `${this.path}#${seg.n}`, ed = this.editors.get(key);
          const src = ed ? ed.live : seg.uml;
          const d = this.diagram(src, cx);
          if (ed && d.state === "ok") ed.lastOk = d;
          const shown = d.state === "ok" ? d : ed?.lastOk;   // keep the last good picture while typing
          const btn = (id, label, color, onClick) => div().id(`rv-b${i}-${id}`).px(6).py(2).rounded(4).text_size(11)
            .text_color(color).hover((x) => x.bg(t.raised)).on_click(onClick).child(label);
          const dirty = ed && ed.state.value() !== ed.saved;
          const bar2 = h_flex().id(`rv-ub${i}`).gap(4).items_center().justify_end()
            .when(this.flash?.key === key, (el) => el.child(div().text_size(11).text_color(t.accent).child(this.flash.msg)))
            .when(dirty, (el) => el.child(div().text_size(11).text_color(t.dim).child("saving…")))
            .child(btn("code", ed ? "hide code" : "code", ed ? t.accent : t.muted, (_e, cx) => this.toggleEditor(key, seg.uml, seg.n, cx)))
            .child(btn("copy", "copy image", t.muted, (_e, cx) => this.copyImage(key, src, cx)));
          const lines = ed ? Math.min(Math.max(ed.state.value().split("\n").length, 4), 18) : 0;
          const box = (msg, color) => div().my(6).p(8).rounded(4).bg(t.raised).text_size(11).text_color(color).child(msg);
          return v_flex().id(`rv-uml-${i}`).py(4).gap(4).child(bar2)
            .when(ed !== undefined, (el) => el.child(div().h(lines * 17 + 12).border_1().border_color(t.border).rounded(4).child(Editor.new(ed.state))))
            .when(!!shown, (el) => el.child(div().py(2).child(image(`${UML_REL}/${shown.id}.png`).w(shown.w).h(shown.h))))
            .when(d.state === "pending" && !shown, (el) => el.child(box("Rendering diagram…", t.dim)))
            .when(d.state === "error", (el) => el.child(box(`PlantUML error: ${d.error}`, t.accent)));
        })));
  }
}
