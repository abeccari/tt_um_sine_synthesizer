import math
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

W = 12                 # register width (bits)
FRAC = W - 2           # fractional bits -> Q2.6
SCALE = 1 << FRAC
MASK = 2 ** W - 1 # 1111111 for W=7
SIGN = 1 << (W - 1) # 1000000 for W=7

ATAN_TABLE = [round(math.atan(2 ** -k) / (2 * math.pi) * (1 << W)) & MASK for k in range(32)]


def wrap(v: int) -> int:
    """Truncate to W bits (models a hardware register capturing a result)."""
    return v & MASK


def to_fixed(x: float) -> int:
    """Float -> W-bit two's-complement fixed point."""
    v = round(x * SCALE)
    v = v - (1 << W) if v & SIGN else v
    return v & MASK


def to_float(v:int) -> float:
    """W-bit two's-complement fixed point -> float."""

    if v & SIGN:
        v = v - (1 << W)
    return float(v) / SCALE


def sar(v: int, k: int) -> int:
    """Arithmetic shift right of a W-bit two's-complement value."""
    if v & SIGN:
        v = v - (1 << W)
    v = v >> k
    return wrap(v)


def x_iter(n_iter: int):
    """
    Compute the CORDIC scaling factor.
    """
    K = math.prod(1.0 / math.cos(math.atan(2.0 ** -i)) for i in range(n_iter))
    return to_fixed(1 / K)


def quadrant_lookup(N: int) -> tuple[bool, int, int]:
    """
    Evaluate which quadrant N falls into to assign signs and 
    decide whether to swap sin and cos.
    """

    quad = N >> (W - 2)

    if quad == 0:
        return False, 1, 1
    if quad == 1:
        return True, -1, 1
    if quad == 2:
        return False, -1, -1  
    if quad == 3:
        return True, 1, -1


def cordic_restricted(
    N: int, n_iter: int, trace: list[tuple[int, int]] | None = None
) -> tuple[float, float]:
    """(cos, sin) of N, a phase code in [0, 2^(W-2)) covering one quadrant
    [0, pi/2). Full turn = 2^W.

    If `trace` is given, the running (x, y) register pair is appended to it
    after the seed and after every iteration -- an instrumentation hook only,
    it does not change the arithmetic. Values are the raw W-bit codes; run
    them through `to_float` to get the vector components."""

    if N < 0 or N >= 2 ** (W - 2):
        raise ValueError(f"N must be in [0, {2 ** (W - 2)}), got {N}")

    x = x_iter(n_iter)
    y = 0
    z = wrap(N)

    if trace is not None:
        trace.append((x, y))   # seed vector: (1/K, 0)

    for k in range(n_iter):
        sigma = 1 if not(z & SIGN) else -1

        if sigma > 0:
            xp = wrap(x - sar(y, k))
            y = wrap(y + sar(x, k))
            x = xp
            z = wrap(z - ATAN_TABLE[k])
        else:
            xp = wrap(x + sar(y, k))
            y = wrap(y - sar(x, k))
            x = xp
            z = wrap(z + ATAN_TABLE[k])

        if trace is not None:
            trace.append((x, y))   # vector after micro-rotation k

    return to_float(x), to_float(y)


def cordic_general(N:int, n_iter:int) -> tuple[float, float]:
    """
    Compute (cos, sin) for N over [0, 2^W).
    """

    swap, sx, sy = quadrant_lookup(N)
    M = N & (2 ** (W - 2) - 1)

    x, y = cordic_restricted(M, n_iter)

    if swap:
        sine, cosine = sy * x, sx * y
    else:
        sine, cosine = sy * y, sx * x
    
    return cosine, sine


if __name__ == "__main__":

    print(f"ATAN_TABLE: {ATAN_TABLE}")

    turns = range(4 * MASK)
    angles = [t * math.pi * (2 ** -(W - 1)) for t in turns]
    turns = [wrap(t) for t in turns] # Resets the counter
    reference = np.cos(angles)

    fig, (ax_curves, ax_rms) = plt.subplots(1, 2, figsize=(12, 5))

    # Left: ground truth vs. CORDIC approximation for a few iteration counts.
    sines = np.zeros_like(angles)
    cosines = np.zeros_like(angles)
    for n in range(max(2, W - 8), W - 1):
        for k, turn in enumerate(turns):
            cosines[k], sines[k] = cordic_general(turn, n)
        ax_curves.plot(angles, cosines, label=f"CORDIC {n} iterations")

    ax_curves.plot(angles, reference, '--k', label="numpy")
    ax_curves.legend()
    ax_curves.set_xlabel("Angle (rad)")
    ax_curves.set_ylabel("Amplitude")
    ax_curves.set_title(f"Ground truth vs. approximation ({W} bits)")

    # Right: RMS residual of the sine approximation vs. number of iterations.
    iter_counts = list(range(1, 16))
    rms_residuals = []
    for n in iter_counts:
        approx = np.array([cordic_general(turn, n)[0] for turn in turns])
        rms_residuals.append(np.sqrt(np.mean((approx - reference) ** 2)))

    ax_rms.semilogy(iter_counts, rms_residuals, 'o-')
    ax_rms.set_xlabel("Number of iterations")
    ax_rms.set_ylabel("RMS residual (cosine)")
    ax_rms.set_title("Convergence")
    ax_rms.grid(True, which="both", ls=":")

    fig.tight_layout()

    # ------------------------------------------------------------------
    # Polar view: ONE (cos, sin) evaluation, iteration by iteration.
    # We trace the raw first-quadrant rotator (cordic_restricted) as it walks
    # a seed vector toward a target angle. The picture shows two things at once:
    #   * the vector SPIRALS OUTWARD -- it starts at r = 1/K (not 1), because
    #     each shift-add micro-rotation also stretches the vector; the 1/K
    #     pre-scale is chosen so it lands at r ~= 1 after exactly n_iter steps.
    #   * the ANGLE oscillates in toward the target: sign(z) makes each step
    #     over- or under-shoot, and the swings shrink as k grows.
    # ------------------------------------------------------------------
    TARGET_DEG = 65.0
    N_ITER_POLAR = 8
    N_code = round(TARGET_DEG / 360.0 * (1 << W))   # angle -> phase code

    trace: list[tuple[int, int]] = []
    cordic_restricted(N_code, N_ITER_POLAR, trace)   # fills `trace` in place

    xs = np.array([to_float(x) for x, _ in trace])
    ys = np.array([to_float(y) for _, y in trace])
    r = np.hypot(xs, ys)
    theta = np.arctan2(ys, xs)
    target = math.radians(TARGET_DEG)

    figp = plt.figure(figsize=(6.5, 7.5), constrained_layout=True)
    figp.set_constrained_layout_pads(hspace=0.08)   # open the gap between the two panels
    gs = figp.add_gridspec(2, 1, height_ratios=[1, 0.4])
    axp = figp.add_subplot(gs[0], projection="polar")     # top   : the rotation
    axc = figp.add_subplot(gs[1])                          # bottom: convergence (~40% height)
    axp.plot(target, 1.0, marker="*", ms=21, color="red", ls="none", zorder=10)

    # references: the ideal unit circle, the target radial, the exact endpoint
    arc = np.linspace(0, math.pi / 2, 200)
    axp.plot(arc, np.ones_like(arc), color="0.8", lw=1, label="unit circle (r = 1)")
    axp.plot([target, target], [0, 1], color="0.6", ls="--", lw=1,
             label=f"target angle ({TARGET_DEG:.0f}°)")

    # the CORDIC trajectory, one marker per iteration, annotated with k.
    # Alternate the label offset so the tightly-converged late points (which
    # pile up near the target) don't overprint each other.
    axp.plot(theta, r, "-o", color="C0", lw=1.5, ms=5, label="CORDIC vector")
    for k, (t, rr) in enumerate(zip(theta, r)):
        dy = 7 if k % 2 == 0 else -11
        axp.annotate(f"k={k}" if k == 0 else str(k), (t, rr),
                     textcoords="offset points", xytext=(7, dy),
                     fontsize=8, color="C0",
                     fontweight="bold" if k in (0, len(theta) - 1) else "normal")

    axp.set_thetamin(0)
    axp.set_thetamax(90)
    axp.set_rmax(1.12)                          # linear radius
    axp.set_rticks([r[0], 1.0])                 # seed radius (1/K) and unit
    axp.set_rlabel_position(88)
    axp.grid(True, ls=":")
    axp.set_title(
        f"CORDIC rotation toward {TARGET_DEG:.0f}°  ({N_ITER_POLAR} iterations, {W}-bit)",
        pad=18,
    )
    axp.legend(loc="lower left", bbox_to_anchor=(-0.05, -0.02), fontsize=8,
               framealpha=0.9)

    # ---- companion panel: how fast the angle converges -----------------
    # This is where the high-order iterations become visible: their info is
    # in the ANGLE, not the radius. Each step corrects by +/-atan(2^-k), so
    # the residual error to the target falls geometrically -> a near-straight
    # descent on a log y-axis (jagged, because sign(z) over/under-shoots).
    # The step size atan(2^-k) is the rough envelope: the residual tracks the
    # current rotation granularity (a lucky over-shoot can briefly dip below
    # it, e.g. k=3). This is the "why N iterations" argument -- the error
    # floor for an N-step rotator is on the order of atan(2^-N).
    ks = np.arange(len(theta))
    angle_err = np.abs(np.degrees(theta) - TARGET_DEG)          # |theta_k - target|, deg
    step = np.degrees([math.atan(2.0 ** -k) for k in ks])       # per-step correction

    axc.semilogy(ks, angle_err, "o-", color="C0",
                 label="angle error  |θ$_k$ − target|")
    axc.semilogy(ks, step, ":", color="0.5",
                 label="step size  atan(2$^{-k}$)")
    axc.set_xlabel("iteration k")
    axc.set_ylabel("degrees")
    axc.set_xticks(ks)
    axc.set_title("Angle convergence")
    axc.grid(True, which="both", ls=":")
    axc.legend(fontsize=8)

    # constrained_layout (set on the figure) handles spacing; no tight_layout,
    # which warns on polar axes.
    out_png = Path(__file__).resolve().parent / "cordic_rotation.png"
    figp.savefig(out_png, dpi=150, bbox_inches="tight")
    print(f"wrote {out_png}")

    plt.show()