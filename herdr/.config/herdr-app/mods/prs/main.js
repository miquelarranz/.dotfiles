// Pull requests: my open pull requests, grouped by the state of their checks (via the gh CLI).
// Open with the `mod.open:prs` command, or as a block in a layout.
import { View, div, svg } from "gpui-kit";
import { v_flex, h_flex } from "gpui-base";
import { exec, theme, closeOverlay, props } from "herdr";
import { FIELDS as ALL_FIELDS, SECTIONS, parse, sections, size, tags, strip, duration, isStale } from "./model.js";

// reviewRequests needs the read:org scope (team names), which this token lacks; it only drives "Your review".
const FIELDS = ALL_FIELDS.split(",").filter((f) => f !== "reviewRequests").join(",");
const ART = "mods/prs/";
const glyph = (name, s = 14) => svg(`${ART}${name}.svg`).size(s).flex_none();
const SQUARE = 6;
const MAX_SQUARES = 10;
const MAX_TAGS = 3;
const tone = (t, key) => (key === "pass" ? t.ansi2 ?? t.done : t[key] ?? t.muted);
// Interactive login shell: the GUI app doesn't inherit GH_TOKEN, your zshrc sets it.
const gh = (args, options) => exec("zsh", ["-lic", 'exec gh "$@"', "gh", ...args], { timeout: 60000, ...options });

export default class Prs extends View {
  init(_p, cx) {
    this.prs = []; this.error = null; this.loading = false; this.fetched = null;
    this.folded = new Set(); this.viewer = null;
    this.refresh(cx);
    cx.timer.every(120000, (cx) => this.refresh(cx));
  }

  async fetchRepo(repo) {
    const r = await gh(["pr", "list", "--repo", repo, "--author", "@me", "--state", "open", "--limit", "100", "--json", FIELDS]);
    if (r.code !== 0) throw new Error((r.stderr || r.stdout).trim().slice(0, 200) || `gh failed for ${repo}`);
    return parse(r.stdout, this.viewer, repo);
  }

  refresh(cx) {
    if (this.loading) return;
    this.loading = true; cx.notify();
    cx.spawn(async (cx) => {
      try {
        if (!this.viewer) {
          const me = await gh(["api", "user", "--jq", ".login"]);
          this.viewer = me.stdout.trim();
        }
        const found = await gh(["search", "prs", "--author", "@me", "--state", "open", "--archived=false", "--limit", "100",
          "--json", "repository", "--jq", "[.[].repository.nameWithOwner]|unique|.[]"]);
        if (found.code !== 0) throw new Error((found.stderr || found.stdout).trim().slice(0, 200) || "gh failed");
        const repos = found.stdout.split("\n").map((l) => l.trim()).filter(Boolean);
        const lists = await Promise.all(repos.map((r) => this.fetchRepo(r)));
        this.prs = lists.flat();
        this.error = null; this.fetched = new Date();
      } catch (e) { this.error = String(e.message ?? e); }
      finally { this.loading = false; cx.notify(); }
    });
  }

  open(url) {
    exec("open", [url]);
    props()?.overlay && closeOverlay(props()?.overlay);
  }

  stateMark(t, state, s = 15) {
    if (state === "failing") return glyph("fail", s).text_color(t.blocked);
    if (state === "running") return glyph("spinner", s).text_color(t.working).spin(900);
    if (state === "passing") return glyph("pass", s).text_color(tone(t, "pass"));
    return div().size(s).flex_none().flex().items_center().justify_center()
      .child(div().size(Math.round(s / 2)).rounded(s).border(1.5).border_color(t.dim));
  }

  pill(t, text, color, outline = false, tip = "") {
    return div().flex_none().h(16).px(5).rounded(4).flex().items_center().text_size(10).font_medium()
      .text_color(color).when(outline, (el) => el.border(1).border_color(t.border)).when(!outline, (el) => el.bg(color + "1f"))
      .when(Boolean(tip), (el) => el.tooltip(tip)).child(text);
  }

  tagPills(t, pr, now) {
    const big = size(pr);
    const all = tags(pr, now);
    const shown = all.slice(0, MAX_TAGS);
    const hidden = all.slice(MAX_TAGS);
    return [
      this.pill(t, big.label, tone(t, big.color), true, big.tip),
      ...shown.map((tag) => this.pill(t, tag.label, tone(t, tag.color), Boolean(tag.outline), tag.tip)),
      hidden.length ? this.pill(t, `+${hidden.length}`, t.muted, true, hidden.map((tag) => tag.label).join(", ")) : null,
    ].filter(Boolean);
  }

  checkStrip(t, pr) {
    if (!pr.checks.length) return null;
    const { shown, more } = strip(pr.checks, MAX_SQUARES);
    const n = pr.counts;
    const label = [n.fail && `${n.fail} failing`, n.running && `${n.running} running`, n.pass && `${n.pass} passed`, n.skip && `${n.skip} skipped`].filter(Boolean).join(", ");
    const square = (c) => {
      const el = div().size(SQUARE).flex_none().rounded(1.5);
      if (c.state === "fail") return el.bg(t.blocked);
      if (c.state === "running") return el.bg(t.working).loop("opacity", { from: 0.35, to: 1, period: 1400 });
      if (c.state === "pass") return el.bg(tone(t, "pass"));
      return el.border(1).border_color(t.dim);
    };
    return h_flex().id(`strip-${pr.id}`).flex_none().gap(2).items_center().tooltip(label)
      .children(shown.map(square))
      .when(more > 0, (el) => el.child(div().ml(2).text_size(10).text_color(t.dim).child(`+${more}`)));
  }

  row(t, pr, now) {
    const stale = isStale(pr, now);
    return h_flex().id(`pr-${pr.id}`).flex_none().min_w(0).pl(8).pr(6).py(6).gap(8).items_start()
      .rounded(6).cursor_pointer().hover((s) => s.bg(t.hover))
      .on_click(() => this.open(pr.url))
      .child(div().mt(1).flex_none().child(this.stateMark(t, pr.state)))
      .child(v_flex().flex_1().min_w(0).gap(3)
        .child(h_flex().h(18).min_w(0).gap(6).items_center()
          .child(div().flex_none().text_size(11).text_color(t.dim).child(`${pr.repo.split("/")[1]}#${pr.number}`))
          .child(div().flex_1().min_w(0).truncate().text_size(12.5).text_color(pr.draft ? t.muted : t.foreground).child(pr.title))
          .when(Boolean(pr.updated), (el) => el.child(div().flex_none().text_size(10.5).text_color(stale ? t.working : t.dim).child(duration(now - pr.updated)))))
        .child(h_flex().h(16).min_w(0).gap(5).items_center()
          .child(h_flex().flex_1().min_w(0).gap(5).items_center().overflow_hidden().children(this.tagPills(t, pr, now)))
          .when(pr.checks.length > 0, (el) => el.child(this.checkStrip(t, pr)))));
  }

  section(t, s) {
    const open = !this.folded.has(s.id);
    return h_flex().id(`section-${s.id}`).h(24).flex_none().pl(10).pr(8).gap(6).items_center()
      .cursor_pointer().rounded(5).text_size(10.5).font_medium().hover((x) => x.bg(t.hover))
      .on_click((_e, cx) => { this.folded.has(s.id) ? this.folded.delete(s.id) : this.folded.add(s.id); cx.notify(); })
      .child(div().text_color(tone(t, s.color)).child(s.label))
      .child(div().text_color(t.dim).child(String(s.prs.length)))
      .child(div().flex_1())
      .child(glyph(open ? "chevron-down" : "chevron-right", 10).text_color(t.dim));
  }

  render(cx) {
    const t = theme();
    const now = Date.now();
    const overlay = props()?.overlay;
    const groups = sections(this.prs);
    const children = [];
    for (const s of groups) {
      children.push(this.section(t, s));
      if (!this.folded.has(s.id)) for (const pr of s.prs) children.push(this.row(t, pr, now));
    }
    if (!groups.length) children.push(div().px(12).py(10).text_size(12).text_color(t.muted).child(this.loading ? "Loading…" : "No open pull requests"));
    const status = this.error ? this.error : this.loading && !this.fetched ? "Loading…"
      : `${this.prs.length} open · ${this.fetched ? this.fetched.toLocaleTimeString() : ""}`;
    return v_flex().size_full().bg(overlay ? t.popover : t.surface).text_color(t.foreground)
      .child(h_flex().px(12).py(8).gap(8).items_center()
        .child(div().text_size(13).flex_1().child("Pull requests"))
        .child(div().text_size(11).text_color(this.error ? t.ansi1 : t.dim).truncate().child(status))
        .child(div().id("prs-refresh").px(6).py(2).rounded(5).text_size(11).text_color(t.dim).cursor_pointer()
          .hover((s) => s.bg(t.hover)).on_click((_e, cx) => this.refresh(cx)).child("↻"))
        .when(Boolean(overlay), (e) => e.child(div().id("prs-esc").px(6).py(2).rounded(5).text_size(11).text_color(t.dim).cursor_pointer()
          .hover((s) => s.bg(t.hover)).on_click(() => closeOverlay(overlay)).child("Esc"))))
      .child(v_flex().flex_1().min_h(0).overflow_y_scroll().px(6).gap(1).children(children));
  }
}
