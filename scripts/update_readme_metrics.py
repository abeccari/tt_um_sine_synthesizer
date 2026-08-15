#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Render the README hardening/timing tables from a LibreLane metrics.json.

Single source of truth: the numbers come straight from the hardening run's
`runs/wokwi/final/metrics.json` (uploaded by the `gds` workflow inside the
`GDS_logs` artifact), plus the clock period from `src/config.json`. The script
rewrites only the region of the README between the markers

    <!-- METRICS:START ... -->
    <!-- METRICS:END -->

so the surrounding prose is never touched. Missing metric keys are a hard error
(a silent wrong number is worse than a loud failure).

Usage (stdlib only, so `uv run` needs no extra deps):

    # pull the latest successful gds run's artifact via gh, then regenerate:
    uv run scripts/update_readme_metrics.py --fetch

    # use a metrics.json you already have on disk:
    uv run scripts/update_readme_metrics.py --metrics runs/wokwi/final/metrics.json

    # CI-friendly: fail (exit 1) if the README block is stale, don't rewrite:
    uv run scripts/update_readme_metrics.py --fetch --check

`--fetch` shells out to the GitHub CLI (`gh`), so you must have it installed and
authenticated (`gh auth login`). It downloads the `GDS_logs` artifact of the most
recent successful `gds.yaml` run on `main` into a temp dir and finds metrics.json.
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


# --- gh artifact fetch --------------------------------------------------------

def _run(cmd: list[str]) -> str:
    """Run a command, returning stdout; turn failures into clean messages."""
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()
    except FileNotFoundError:
        raise SystemExit(
            f"'{cmd[0]}' not found. Install the GitHub CLI and authenticate: gh auth login")
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"command failed: {' '.join(cmd)}\n{(e.stderr or '').strip()}")


def fetch_metrics(dest_dir: str, repo: str | None, workflow: str,
                  branch: str, artifact: str) -> str:
    """Download the latest successful run's artifact via gh; return metrics.json path."""
    if repo is None:                                   # infer from the checkout's origin
        repo = _run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    run_id = _run(["gh", "run", "list", "--repo", repo, "--workflow", workflow,
                   "--branch", branch, "--status", "success", "-L", "1",
                   "--json", "databaseId", "--jq", ".[0].databaseId"])
    if not run_id:
        raise SystemExit(f"no successful '{workflow}' run found on '{branch}' in {repo}")
    print(f"fetching '{artifact}' from {repo} run {run_id} …", file=sys.stderr)
    _run(["gh", "run", "download", run_id, "--repo", repo,
          "--name", artifact, "--dir", dest_dir])
    # Locate metrics.json regardless of how gh nests the extracted artifact.
    hits = sorted(Path(dest_dir).rglob("metrics.json"))
    chosen = [p for p in hits if p.parent.name == "final"] or hits
    if not chosen:
        raise SystemExit(f"no metrics.json found inside artifact '{artifact}'")
    return str(chosen[0])


# --- rendering ----------------------------------------------------------------

def _require(metrics: dict, key: str):
    """Fetch a metric key, failing loudly if the schema moved under us."""
    if key not in metrics:
        raise KeyError(
            f"metric '{key}' not found in metrics.json -- the LibreLane schema "
            f"may have changed; update scripts/update_readme_metrics.py")
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
        f"| Utilisation | {util:.1f}% |",
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


# --- entry point --------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Regenerate the README Results tables.")
    src = ap.add_argument_group("metrics source")
    src.add_argument("--fetch", action="store_true",
                     help="download the latest gds artifact via gh instead of reading a local file")
    src.add_argument("--metrics", default=str(REPO / "runs/wokwi/final/metrics.json"),
                     help="local metrics.json (ignored when --fetch is given)")
    gh = ap.add_argument_group("gh options (with --fetch)")
    gh.add_argument("--repo", default=None, help="OWNER/NAME (default: inferred by gh)")
    gh.add_argument("--workflow", default="gds.yaml")
    gh.add_argument("--branch", default="main")
    gh.add_argument("--artifact", default="GDS_logs")
    ap.add_argument("--config", default=str(REPO / "src/config.json"))
    ap.add_argument("--readme", default=str(REPO / "README.md"))
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the README block is stale; do not write")
    args = ap.parse_args()

    tmp = None
    try:
        if args.fetch:
            tmp = tempfile.mkdtemp(prefix="ttmetrics-")
            metrics_path = fetch_metrics(tmp, args.repo, args.workflow, args.branch, args.artifact)
        else:
            metrics_path = args.metrics

        with open(metrics_path) as f:
            metrics = json.load(f)
        with open(args.config) as f:
            period_ns = json.load(f)["CLOCK_PERIOD"]
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    block = render_block(metrics, period_ns)
    with open(args.readme) as f:
        readme = f.read()
    updated = splice(readme, block)

    if args.check:
        if updated != readme:
            print("README metrics block is STALE -- run scripts/update_readme_metrics.py",
                  file=sys.stderr)
            return 1
        print("README metrics block is up to date.")
        return 0

    if updated != readme:
        with open(args.readme, "w") as f:
            f.write(updated)
        print(f"README metrics block updated from {metrics_path}")
    else:
        print("README metrics block already up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
