// New tab in the main checkout (repo root) of the focused pane's repo,
// even when the pane is in a git worktree. Outside a repo: FALLBACK.
// Bound in keys.json as "mod.open:newtab-root"; it acts at once and closes itself.

import { View, div } from "gpui-kit";
import { exec, focused, run, props, closeOverlay, home } from "herdr";

export default class NewTabRoot extends View {
  init(p, cx) {
    const overlay = (p && p.overlay) ?? props()?.overlay;
    cx.spawn(async (cx) => {
      let folder = null;
      try {
        const cwd = focused()?.cwd;
        if (cwd) {
          const r = await exec("git", ["rev-parse", "--path-format=absolute", "--git-common-dir"], { cwd, timeout: 5000 });
          const common = r.stdout.trim();
          if (r.code === 0 && common.endsWith("/.git")) folder = common.slice(0, -"/.git".length);
        }
      } catch (e) {}
      if (overlay) closeOverlay(overlay);
      run("tab.new", { folder: folder ?? `${home()}/code` });
    });
  }

  render(cx) {
    return div().size_full();
  }
}
