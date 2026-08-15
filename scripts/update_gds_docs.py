#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Refresh local docs from the latest Tiny Tapeout `gds` build artifacts.

Two things, independently or together:
  * README Results tables  <- `GDS_logs` artifact (`runs/wokwi/final/metrics.json`)
                              + clock period from `src/config.json`
  * docs/tile_gds_wip.png  <- `gds_render` artifact (`gds_render.png`)

The README tables are rewritten only between the markers

    <!-- METRICS:START ... -->
    <!-- METRICS:END -->

so surrounding prose is never touched. Missing metric keys are a hard error
(a silent wrong number is worse than a loud failure).

Usage (stdlib only, so `uv run` needs no extra deps):

    uv run scripts/update_gds_docs.py --fetch             # README metrics from latest gds run
    uv run scripts/update_gds_docs.py --render            # refresh docs/tile_gds_wip.png
    uv run scripts/update_gds_docs.py --fetch --render    # both, from the SAME run
    uv run scripts/update_gds_docs.py --metrics <path>    # README from a local metrics.json
    uv run scripts/update_gds_docs.py --render-src <png>  # render from a local image
    uv run scripts/update_gds_docs.py --fetch --check     # CI: exit 1 if README block is stale

`--fetch`/`--render` shell out to the GitHub CLI (`gh`), so it must be installed and
authenticated (`gh auth login`). Both pull from the most recent successful `gds.yaml`
run on `main`; when combined they resolve that run once and download both artifacts
from it, so the metrics and the picture always come from the same build.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

START = "<!-- METRICS:START"
END = "<!-- METRICS:END -->"

# Anchor default paths to the repo root (this file lives in <repo>/scripts/),
# so the script works regardless of the current working directory.
REPO = Path(__file__).resolve().parent.parent
DEFAULT_METRICS = REPO / "runs/wokwi/final/metrics.json"
DEFAULT_RENDER_DEST = REPO / "docs/tile_gds_wip.png"


# --- gh artifact fetch --------------------------------------------------------

def _gh(cmd: list[str]) -> str:
    """Run a command, returning stdout; turn failures into clean messages."""
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()
    except FileNotFoundError:
        raise SystemExit(
            f"'{cmd[0]}' not found. Install the GitHub CLI and authenticate: gh auth login")
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"command failed: {' '.join(cmd)}\n{(e.stderr or '').strip()}")


def _resolve_repo(repo: str | None) -> str:
    return repo or _gh(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])


def _latest_run_id(repo: str, workflow: str, branch: str) -> str:
    run_id = _gh(["gh", "run", "list", "--repo", repo, "--workflow", workflow,
                  "--branch", branch, "--status", "success", "-L", "1",
                  "--json", "databaseId", "--jq", ".[0].databaseId"])
    if not run_id:
        raise SystemExit(f"no successful '{workflow}' run found on '{branch}' in {repo}")
    return run_id


def _download(repo: str, run_id: str, artifact: str, dest: str) -> str:
    print(f"fetching '{artifact}' from {repo} run {run_id} …", file=sys.stderr)
    _gh(["gh", "run", "download", run_id, "--repo", repo, "--name", artifact, "--dir", dest])
    return dest


def _find(root: str, filename: str, prefer_parent: str | None = None) -> str:
    """Locate a file inside a downloaded artifact, regardless of nesting."""
    hits = sorted(Path(root).rglob(filename))
    if prefer_parent:
        hits = [p for p in hits if p.parent.name == prefer_parent] or hits
    if not hits:
        raise SystemExit(f"'{filename}' not found inside the downloaded artifact")
    return str(hits[0])


# --- README metrics rendering -------------------------------------------------

def _require(metrics: dict, key: str):
    """Fetch a metric key, failing loudly if the schema moved under us."""
    if key not in metrics:
        raise KeyError(
            f"metric '{key}' not found in metrics.json -- the LibreLane schema "
            f"may have changed; update scripts/update_gds_docs.py")
    return metrics[key]


def _corner_label(corner: str) -> str:
    """'nom_slow_1p08V_125C' -> 'slow, 1.08 V, 125 C'  (parsed, not hard-coded)."""
    speed = next((s for s in ("slow", "typ", "fast") if s in corner), corner)
    speed = {"slow": "slow", "typ": "typical", "fast": "fast"}[speed]

    mv = re.search(r"(\d+)p(\d+)V", corner)
    volts = f"{mv.group(1)}.{mv.group(2)} V" if mv else "?"

    mt = re.search(r"(m?)(\d+)C", corner)
    temp = f"{'−' if mt.group(1) else ''}{mt.group(2)} °C" if mt else "?"  # U+2212 minus
    return f"{speed}, {volts}, {temp}"


def _corners(metrics: dict) -> list[str]:
    """All corner suffixes present on the setup worst-slack metric."""
    pat = re.compile(r"^timing__setup__ws__corner:(.+)$")
    return sorted(m.group(1) for k in metrics if (m := pat.match(k)))


def render_block(metrics: dict, period_ns: float) -> str:
    # design__instance__utilization is the FINAL placement density (incl. the clock
    # tree + timing/hold-repair cells the flow inserts). Note the TT summary page shows
    # a LOWER number: it scrapes OpenROAD's global-placement log (GPL-0019), an earlier
    # stage before those cells exist. Same run, different point in the flow.
    util = _require(metrics, "design__instance__utilization") * 100.0
    stdcells = _require(metrics, "design__instance__count__stdcell")
    ffs = _require(metrics, "design__instance__count__class:sequential_cell")
    latches = _require(metrics, "design__inferred_latch__count")
    lint_e = _require(metrics, "design__lint_error__count")
    lint_w = _require(metrics, "design__lint_warning__count")
    drc = _require(metrics, "magic__drc_error__count")
    lvs = _require(metrics, "design__lvs_error__count")
    antenna = _require(metrics, "antenna__violating__nets")
    power_mw = _require(metrics, "power__total") * 1e3
    target_mhz = 1e3 / period_ns

    # Per-corner timing, worst (slow) first.
    corners = sorted(
        _corners(metrics),
        key=lambda c: _require(metrics, f"timing__setup__ws__corner:{c}"))
    setup_vio = sum(metrics.get(f"timing__setup_vio__count__corner:{c}", 0) for c in corners)
    hold_vio = sum(metrics.get(f"timing__hold_vio__count__corner:{c}", 0) for c in corners)
    clean = (setup_vio == 0 and hold_vio == 0)
    status = ("closed with **zero setup and hold violations at all three corners**"
              if clean else
              f"**{setup_vio} setup / {hold_vio} hold violations**")

    lat = "none" if latches == 0 else str(latches)
    ok = (drc == 0 and lvs == 0 and antenna == 0)
    signoff = (f"clean (magic DRC {drc}, LVS {lvs}, antenna {antenna})" if ok
               else f"magic DRC {drc}, LVS {lvs}, antenna {antenna}")

    rows = [
        f"**Hardening** (LibreLane, IHP SG13G2, 1×1 tile, "
        f"{period_ns:g} ns / {target_mhz:g} MHz target):",
        "",
        "| | |",
        "|---|---|",
        f"| Utilisation (final placement) | {util:.1f}% |",
        f"| Standard cells | {stdcells} (excl. fill) |",
        f"| Flip-flops | {ffs} |",
        f"| Inferred latches | {lat} |",
        f"| Lint | {lint_e} errors, {lint_w} warnings |",
        f"| DRC / LVS / antenna | {signoff} |",
        f"| Power (typ) | ~{power_mw:.2f} mW |",
        "",
        f"**Timing** — {status} ({period_ns:g} ns period):",
        "",
        "| Corner | Setup slack | Critical path | Implied f_max |",
        "|---|---|---|---|",
    ]
    for c in corners:
        ws = _require(metrics, f"timing__setup__ws__corner:{c}")
        crit = period_ns - ws
        fmax = 1e3 / crit if crit > 0 else float("inf")
        rows.append(f"| {_corner_label(c)} | {ws:.2f} ns | {crit:.2f} ns | ≈ {fmax:.0f} MHz |")
    return "\n".join(rows)


def splice(readme: str, block: str) -> str:
    m = re.search(re.escape(START) + r".*?-->", readme)
    if not m or END not in readme:
        raise SystemExit(
            f"markers not found in README. Add a block delimited by\n"
            f"    {START} ... -->\n    {END}\n"
            f"where the tables should be generated.")
    head = readme[:m.end()]
    tail = readme[readme.index(END):]
    return f"{head}\n{block}\n{tail}"


def update_readme(metrics_path: str, config_path: str, readme_path: str, check: bool) -> int:
    with open(metrics_path) as f:
        metrics = json.load(f)
    with open(config_path) as f:
        period_ns = json.load(f)["CLOCK_PERIOD"]
    block = render_block(metrics, period_ns)
    with open(readme_path) as f:
        readme = f.read()
    updated = splice(readme, block)

    if check:
        if updated != readme:
            print("README metrics block is STALE -- run scripts/update_gds_docs.py",
                  file=sys.stderr)
            return 1
        print("README metrics block is up to date.")
        return 0
    if updated != readme:
        with open(readme_path, "w") as f:
            f.write(updated)
        print(f"README metrics block updated from {metrics_path}")
    else:
        print("README metrics block already up to date.")
    return 0


# --- entry point --------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Refresh README metrics and/or the GDS render from gds artifacts.")
    act = ap.add_argument_group("what to update (default: README from local metrics)")
    act.add_argument("--fetch", action="store_true",
                     help="pull GDS_logs via gh and refresh the README metrics block")
    act.add_argument("--render", action="store_true",
                     help="pull the gds_render artifact via gh and refresh the render image")
    act.add_argument("--metrics", default=None,
                     help=f"local metrics.json instead of --fetch (default {DEFAULT_METRICS})")
    act.add_argument("--render-src", default=None,
                     help="local PNG instead of --render")
    gh = ap.add_argument_group("gh options (with --fetch / --render)")
    gh.add_argument("--repo", default=None, help="OWNER/NAME (default: inferred by gh)")
    gh.add_argument("--workflow", default="gds.yaml")
    gh.add_argument("--branch", default="main")
    gh.add_argument("--artifact", default="GDS_logs", help="metrics artifact name")
    gh.add_argument("--render-artifact", default="gds_render", help="render artifact name")
    out = ap.add_argument_group("outputs")
    out.add_argument("--config", default=str(REPO / "src/config.json"))
    out.add_argument("--readme", default=str(REPO / "README.md"))
    out.add_argument("--render-dest", default=str(DEFAULT_RENDER_DEST))
    ap.add_argument("--check", action="store_true",
                    help="README only: exit 1 if the block is stale; write nothing")
    args = ap.parse_args()

    do_metrics = args.fetch or args.metrics is not None
    do_render = args.render or args.render_src is not None
    if args.check:
        if do_render:
            print("note: --check only validates the README; skipping render.", file=sys.stderr)
        do_render = False                      # a check never writes / never touches the image
        do_metrics = True                      # --check always validates the README
    elif not do_metrics and not do_render:
        do_metrics = True                      # default action: refresh README from local metrics

    metrics_path = args.metrics or str(DEFAULT_METRICS)
    render_src = args.render_src
    rc = 0
    tmp = None
    try:
        need_metrics_gh = do_metrics and args.fetch
        need_render_gh = do_render and args.render
        if need_metrics_gh or need_render_gh:
            repo = _resolve_repo(args.repo)
            run_id = _latest_run_id(repo, args.workflow, args.branch)
            tmp = tempfile.mkdtemp(prefix="ttgds-")
            if need_metrics_gh:
                d = _download(repo, run_id, args.artifact, f"{tmp}/metrics")
                metrics_path = _find(d, "metrics.json", prefer_parent="final")
            if need_render_gh:
                d = _download(repo, run_id, args.render_artifact, f"{tmp}/render")
                hits = sorted(Path(d).rglob("gds_render.png")) or sorted(Path(d).rglob("*.png"))
                if not hits:
                    raise SystemExit(f"no PNG found inside artifact '{args.render_artifact}'")
                render_src = str(hits[0])

        if do_metrics:
            rc |= update_readme(metrics_path, args.config, args.readme, args.check)

        if do_render:
            Path(args.render_dest).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(render_src, args.render_dest)
            print(f"render updated: {args.render_dest} <- {render_src}")
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
