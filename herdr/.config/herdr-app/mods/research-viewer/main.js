// Research viewer: renders the vault note of the focused Claude session in the right panel.
// A PostToolUse hook (~/.claude/hooks/vault-note-hook.sh -> open-in-herdr.sh) registers every vault
// note a Claude session writes in <appHome>/data/research-viewer/<terminal>.json. This mod follows the focused terminal's note
// and re-reads the file whenever anything under Research/ changes. Revealing the panel is done by
// that script, not here.
import { View, div } from "gpui-kit";
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
    this.recent = [];
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
      this.recent = (await sh('ls -t "$1"/*/*.md 2>/dev/null | head -25', ROOT())).split("\n").filter(Boolean);
    } catch (e) { this.error = String(e); }
    await this.load(this.want(), true, cx);
  }

  // The note this copy should show right now.
  want() {
    if (this.pinned) return this.pinned;
    const f = focused();
    if (!f) return this.path;
    return this.sessionNotes(f)[0]?.path ?? null;
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

  pick(path, cx) { this.pinned = path; this.picker = false; this.load(path, false, cx); cx.notify(); }
  follow_session(cx) { this.pinned = null; this.picker = false; this.load(this.want(), false, cx); cx.notify(); }

  render(cx) {
    const t = theme();
    const root = v_flex().id("rv-root").size_full().bg(t.surface);

    const bar = h_flex().id("rv-bar").px(10).py(6).gap(8).items_center().border_b(1).border_color(t.border)
      .child(div().flex_1().truncate().text_size(13).font_semibold().text_color(t.foreground)
        .child(this.path ? title(this.path) : "Research notes"))
      .child(div().id("rv-pin").px(6).py(2).rounded(4).text_size(11)
        .text_color(this.pinned ? t.accent : t.dim).hover((s) => s.bg(t.raised))
        .on_click((_e, cx) => this.pinned ? this.follow_session(cx) : (this.path && this.pick(this.path, cx)))
        .child(this.pinned ? "pinned" : "following"))
      .child(div().id("rv-recent").px(6).py(2).rounded(4).text_size(11).text_color(t.muted)
        .hover((s) => s.bg(t.raised)).on_click((_e, cx) => { this.picker = !this.picker; cx.notify(); })
        .child("notes"));

    if (this.picker) {
      const mine = this.sessionNotes().map((n) => n.path);
      const rows = [...mine.map((p) => [p, "this session"]), ...this.recent.filter((p) => !mine.includes(p)).map((p) => [p, area(p)])];
      return root.child(bar).child(
        div().flex_1().overflow_y_scrollbar().children(
          rows.map(([p, sub], i) =>
            v_flex().id(`rv-r${i}`).px(10).py(6).hover((s) => s.bg(t.raised)).on_click((_e, cx) => this.pick(p, cx))
              .child(div().text_size(12).text_color(t.foreground).truncate().child(title(p)))
              .child(div().text_size(10).text_color(sub === "this session" ? t.accent : t.dim).child(sub === "this session" ? `${area(p)} · this session` : sub)))));
    }

    if (this.error || this.text === null) {
      return root.child(bar).child(div().p(14).text_size(12).text_color(t.dim)
        .child(this.error ?? "No note for this session yet. Run /investigate, or pick one from “notes”."));
    }

    const { meta, body } = split(this.text);
    const chips = ["area", "type", "status", "updated"].filter((k) => meta[k]).map((k) => meta[k]);
    return root.child(bar)
      .when(chips.length > 0, (el) => el.child(
        h_flex().id("rv-meta").px(10).py(5).gap(6).border_b(1).border_color(t.border).children(
          chips.map((c, i) => div().id(`rv-c${i}`).px(6).py(1).rounded(4).bg(t.raised).text_size(10).text_color(t.muted).child(c)))))
      .child(div().id("rv-body").flex_1().overflow_y_scrollbar().px(12).py(10)
        .child(TextView.markdown("rv-md", plain(body)).selectable(true)));
  }
}
