import math
import numpy as np
import matplotlib.pyplot as plt

W = 7                 # register width (bits)
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


def cordic_restricted(N: int, n_iter: int) -> tuple[float, float]:
    """(cos, sin) of N, a phase code in [0, 2^(W-2)) covering one quadrant
    [0, pi/2). Full turn = 2^W."""

    if N < 0 or N >= 2 ** (W - 2):
        raise ValueError(f"N must be in [0, {2 ** (W - 2)}), got {N}")

    x = x_iter(n_iter)
    y = 0
    z = wrap(N)

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
    for n in range(W - 5, W - 1):
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
    plt.show()