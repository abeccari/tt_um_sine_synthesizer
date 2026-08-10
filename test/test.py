 # SPDX-FileCopyrightText: © 2026 Alberto Beccari
# SPDX-License-Identifier: Apache-2.0
"""Top-level cocotb tests for tt_um_abeccari_swsynth.

Pinout (see src/project.v):
  ui_in[3:0]   = NOTE          ui_in[7:4]   = OCTAVE
  uo_out[6:0]  = SINE (ob)     uo_out[7]    = PDM_I  (sine sigma-delta)
  uio_out[0]   = SAMPLE_EN     uio_out[7]   = PDM_Q  (cosine sigma-delta)
"""

import math
import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles

CLK_HZ = 12_288_000            # 12.288 MHz system clock
CLK_PS = round(1e9 / CLK_HZ)  # clock period in ns (~81.380)
DIV    = 256                   # sample-rate divider -> f_s = clk / 256 = 48 kHz

# Gate-level sim runs the synthesised netlist, where RTL nets (phase_acc,
# phase_inc) don't exist -- skip the whitebox tests that peek at internals.
GL = os.getenv("GATES") == "yes"


async def start_and_reset(dut):
    """Start the clock, apply reset, leave the design running."""
    cocotb.start_soon(Clock(dut.clk, CLK_PS, unit="ns").start())
    dut.ena.value    = 1
    dut.ui_in.value  = 0
    dut.uio_in.value = 0
    dut.rst_n.value  = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value  = 1
    await RisingEdge(dut.clk)

@cocotb.test(skip=GL)
async def test_reset(dut):
    """Asserting rst_n mid-operation forces the design back to its reset state."""
    await start_and_reset(dut)
    dut.ui_in.value = 0x80          # octave 8, note 0: high freq, advances fast

    # let it run so the state is non-trivial
    await ClockCycles(dut.clk, 3 * DIV)
    assert int(dut.user_project.phase_acc.value) != 0, "design never advanced"

    # assert reset while it's running
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 2)

    # everything resettable is back to its reset value
    assert int(dut.user_project.phase_acc.value) == 0, "phase_acc not cleared"
    assert str(dut.uio_out.value)[-1] == "0",          "SAMPLE_EN not low in reset"
    assert str(dut.uo_out.value)[0]  == "0",           "PDM not low in reset"
    assert int(dut.uo_out.value) & 0x7F == 64,         "SINE not at midscale in reset"
    assert str(dut.uio_out.value)[0] == "0",           "PDM_Q not low in reset"

    # while held in reset, SAMPLE_EN must never pulse
    for _ in range(2 * DIV):
        await RisingEdge(dut.clk)
        assert str(dut.uio_out.value)[-1] == "0", "SAMPLE_EN pulsed during reset"

    # release reset
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2 * DIV)
    assert int(dut.user_project.phase_acc.value) != 0, "did not resume after reset"


def sample_en(dut):
    """SAMPLE_EN = uio_out bit 0 (LSB). Read the LSB char so it's X-safe even
    while the other uio_out bits (PDM_Q) are still resolving after reset."""
    return str(dut.uio_out.value)[-1]


@cocotb.test()
async def test_sample_en(dut):
    """SAMPLE_EN is a one-clock-wide strobe every 256 clocks (48 kHz @ 12.288 MHz)."""
    await start_and_reset(dut)

    # Advance to the first rising edge of SAMPLE_EN (it arrives within one period).
    for _ in range(2 * DIV):
        await RisingEdge(dut.clk)
        if sample_en(dut) == "1":
            break
    else:
        assert False, "no SAMPLE_EN pulse within two divider periods"

    # Width: it must be low again on the very next clock (single-cycle pulse).
    await RisingEdge(dut.clk)
    assert sample_en(dut) == "0", "SAMPLE_EN is wider than one clock cycle"

    # Period: count clocks until the next pulse.
    n = 1
    while sample_en(dut) == "0":
        await RisingEdge(dut.clk)
        n += 1
    assert n == DIV, f"SAMPLE_EN period = {n} clocks (expected {DIV})"

    # Frequency follows from the divider and the known clock.
    f = CLK_HZ / n
    dut._log.info(f"SAMPLE_EN: 1-clk pulse every {n} clocks -> {f:.0f} Hz")
    assert f == 48_000, f"SAMPLE_EN frequency {f:.0f} Hz (expected 48000)"

# --- NCO / frequency-map reference (must match src/project.v) -----------------
N_ACC    = 20                                          # phase-accumulator width
MAX_OCT  = 8                                           # freq_map octave clamp
FS       = CLK_HZ / DIV                                # 48 kHz sample rate
SEMITONE = [601, 636, 674, 714, 757, 802, 850, 900, 954, 1010, 1070, 1134]


def expected_phase_inc(note, octv):
    """Replica of freq_map: semitone LUT (note>=12 -> A) shifted by clamped octave."""
    base = SEMITONE[note if note < 12 else 0]
    return base << min(octv, MAX_OCT)


async def step_one_sample(dut):
    """Advance exactly one sample period (256 clocks); return the accumulator."""
    await ClockCycles(dut.clk, DIV)
    return int(dut.user_project.phase_acc.value)


@cocotb.test(skip=GL)
async def test_nco_period(dut):
    """Phase accumulator wraps at the frequency the freq map dictates, for note 0
    across octaves 0..8 and every note in the lowest (0) and highest (8) octave."""
    await start_and_reset(dut)

    cases = sorted(
        {(0, o) for o in range(2, MAX_OCT + 1)}  # note 0, octaves 2 - 8
        | {(0, o) for o in range(9, 16)}         # note 0, octaves 9 - 15 (must clamp to 8)
        | {(nt, 3) for nt in range(12)}          # all notes, octave 3
        | {(nt, MAX_OCT) for nt in range(12)}    # all notes, octave 8
        | {(nt, 15) for nt in range(16)}         # all notes, octave code 15 (must clamp to 8)
    )

    for note, octv in cases:
        inc = expected_phase_inc(note, octv)

        # Reset with the note applied so the accumulator starts at 0 and the
        # 2-FF input synchroniser has settled before it counts.
        dut.ui_in.value = (octv << 4) | note
        dut.rst_n.value = 0
        await ClockCycles(dut.clk, 4)
        dut.rst_n.value = 1
        await RisingEdge(dut.clk)

        # 1) the freq map produced the expected increment (isolates map vs NCO)
        got = int(dut.user_project.phase_inc.value)
        assert got == inc, f"note {note} oct {octv}: phase_inc {got} != {inc}"

        # 2) count sample periods until the accumulator first overflows
        prev, m, timeout = 0, 0, 4000
        while m < timeout:
            cur = await step_one_sample(dut)
            m += 1
            if cur < prev:                # value dropped -> wrapped past 2^N
                break
            prev = cur
        else:
            assert False, f"note {note} oct {octv}: no wrap within {timeout} samples"

        # From phase 0 the first wrap is at exactly ceil(2^N / inc) samples --
        # deterministic integer arithmetic, so NO tolerance vs the NCO's own maths.
        expected = -(-(1 << N_ACC) // inc)            # ceil(2^N / inc)
        assert m == expected, \
            f"note {note} oct {octv}: first wrap at {m} samples, expected {expected}"

        # vs the *ideal* equal-tempered note a small tolerance IS needed, because
        # the LUT rounds phase_inc (<= ~1.3 cents by construction).
        oct_eff = min(octv, MAX_OCT)             # octave codes > 8 clamp to 8
        note_eff = note if note < 12 else 0      # note codes 12-15 fold to A
        f_nco   = inc * FS / (1 << N_ACC)
        f_ideal = 27.5 * 2 ** (note_eff / 12) * 2 ** oct_eff
        cents   = 1200 * math.log2(f_nco / f_ideal)
        dut._log.info(f"note {note:2d} oct {octv}: inc={inc:6d}  f={f_nco:8.2f} Hz  "
                      f"period={m} samp  ({cents:+.2f} cents)")
        assert abs(cents) < 5.0, f"note {note} oct {octv}: {cents:+.2f} cents off ideal"


async def capture_sine(dut, n):
    """Collect n signed SINE samples (uo_out[6:0], offset-binary, midscale 64),
    one per SAMPLE_EN pulse."""
    sine = []
    for _ in range(n):
        while str(dut.uio_out.value)[-1] != "1":     # wait for SAMPLE_EN
            await RisingEdge(dut.clk)
        await ClockCycles(dut.clk, 2)                # let accumulate + CORDIC reg settle
        sine.append((int(dut.uo_out.value) & 0x7F) - 64)
    return sine


@cocotb.test()
async def test_sine(dut):
    """Capture the parallel SINE output (uo_out[6:0]); check spectrum @ f_ideal,
    amplitude, and spectral purity. The cosine is emitted only as PDM_Q, so its
    quadrature with the sine is verified in test_pdm_audio."""
    import os
    import numpy as np

    # Waveform PNGs are opt-in: run `make PLOT_WAVES=1`. Keeps matplotlib off the
    # CI path -- CI runs the numeric checks and never imports it.
    plot = os.environ.get("PLOT_WAVES", "").lower() in ("1", "true", "yes", "y", "on")
    if plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        outdir = os.path.join(os.path.dirname(__file__), "waves")
        os.makedirs(outdir, exist_ok=True)

    await start_and_reset(dut)

    cases = ((0, 3), (4, 8), (15, 15))

    for note, octv in cases:
        inc      = expected_phase_inc(note, octv)
        oct_eff  = min(octv, MAX_OCT)
        note_eff = note if note < 12 else 0
        f_ideal  = 27.5 * 2 ** (note_eff / 12) * 2 ** oct_eff

        dut.ui_in.value = (octv << 4) | note          # reset with note applied
        dut.rst_n.value = 0
        await ClockCycles(dut.clk, 4)
        dut.rst_n.value = 1
        await RisingEdge(dut.clk)

        period = (1 << N_ACC) / inc
        Ns     = int(min(4096, max(512, 12 * period)))   # >= ~12 periods, capped
        sine = await capture_sine(dut, Ns + 4)
        s = np.array(sine[4:], float)                    # drop reset transient
        Ns = len(s)

        dc = s.mean()
        assert abs(dc) < 1.0, f"note {note} oct {octv}: sine DC {dc:+.2f}"

        amp = (s.max() - s.min()) / 2.0     # half peak-to-peak, DC-independent
        assert 55 <= amp <= 64, f"note {note} oct {octv}: amp {amp:.1f}"

        w    = np.hanning(Ns)
        spec = np.abs(np.fft.rfft((s - dc) * w))
        freq = np.fft.rfftfreq(Ns, d=1 / FS)
        k    = int(np.argmax(spec[1:])) + 1              # skip DC
        f_pk, binw = freq[k], FS / Ns
        assert abs(f_pk - f_ideal) < 2 * binw, \
            f"note {note} oct {octv}: peak {f_pk:.1f} Hz vs ideal {f_ideal:.1f} (bin {binw:.1f})"

        spur = spec.copy(); spur[0] = 0; spur[max(1, k - 2):k + 3] = 0
        sfdr = 20 * np.log10(spec[k] / max(spur.max(), 1e-9))
        assert sfdr > 25, f"note {note} oct {octv}: SFDR {sfdr:.1f} dB"

        dut._log.info(f"note {note:2d} oct {octv}: f_pk={f_pk:8.1f} Hz (ideal {f_ideal:8.1f}) "
                      f"amp={amp:.0f} SFDR={sfdr:.1f}dB")

        # waveform + spectrum for visual inspection (opt-in via PLOT_WAVES)
        if plot:
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 6))
            show = min(len(s), int(3 * period) + 2)
            ax1.plot(s[:show], ".-", label="sine")
            ax1.set(title=f"note {note} oct {octv} (f={f_ideal:.1f} Hz)",
                    xlabel="sample", ylabel="code"); ax1.legend(); ax1.grid(True)
            ax2.semilogx(freq, 20 * np.log10(spec / spec.max() + 1e-12))
            ax2.axvline(f_ideal, color="r", ls="--", lw=0.8)
            ax2.set(xlabel="Hz", ylabel="dB", ylim=(-80, 5)); ax2.grid(True)
            fig.tight_layout(); fig.savefig(os.path.join(outdir, f"note{note}_oct{octv}.png"), dpi=90)
            plt.close(fig)


@cocotb.test()
async def test_pdm_audio(dut):
    """Capture both 1-bit PDM streams (I=uo_out[7], Q=uio_out[7]) at the full clock,
    low-pass each at 24 kHz, and check the recovered tones: centre frequency, in-band
    SFDR (I), and I/Q quadrature via their cross-correlation (~0 at zero lag, extremal
    at a quarter period). Amplitude is arbitrary after filtering, so it's not asserted."""
    import os
    import numpy as np

    plot = os.environ.get("PLOT_WAVES", "").lower() in ("1", "true", "yes", "on")
    if plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        outdir = os.path.join(os.path.dirname(__file__), "waves")
        os.makedirs(outdir, exist_ok=True)

    await start_and_reset(dut)

    N     = 1 << 15          # clocks captured (>= 10 periods for octave-8 notes)
    FC    = 24_000           # low-pass cutoff = Nyquist of the 48 kHz audio band
    NTAPS = 1023             # windowed-sinc FIR length
    cases = ((0, 8), (4, 8))

    # FIR low-pass kernel (numpy only), reused across cases
    n  = np.arange(NTAPS) - (NTAPS - 1) / 2
    h  = np.sinc(2 * FC / CLK_HZ * n) * np.hamming(NTAPS)
    h /= h.sum()

    def band_metrics(sig):
        """Peak frequency and SFDR within the audio band [0, FC]."""
        w    = np.hanning(N)
        spec = np.abs(np.fft.rfft((sig - sig.mean()) * w))
        fr   = np.fft.rfftfreq(N, d=1 / CLK_HZ)
        spec[fr > FC] = 0.0
        spec[0] = 0.0
        k = int(np.argmax(spec))
        sp = spec.copy(); sp[max(1, k - 3):k + 4] = 0.0
        return fr[k], 20 * np.log10(spec[k] / max(sp.max(), 1e-9))

    def xcorr(a, b, max_lag):
        """Normalised cross-correlation R(L) = <a(t) b(t+L)> for L in [-max_lag, max_lag].
        For two equal-power signals R lies in [-1, 1]. Returns (lags, R)."""
        a = a - a.mean(); b = b - b.mean()
        norm = math.sqrt(float(a @ a) * float(b @ b)) or 1.0
        lags = np.arange(-max_lag, max_lag + 1)
        R = np.empty(len(lags))
        for j, L in enumerate(lags):
            R[j] = (a[:len(a) - L] @ b[L:]) if L >= 0 else (a[-L:] @ b[:len(b) + L])
        return lags, R / norm

    for note, octv in cases:
        oct_eff  = min(octv, MAX_OCT)
        note_eff = note if note < 12 else 0
        f_ideal  = 27.5 * 2 ** (note_eff / 12) * 2 ** oct_eff

        dut.ui_in.value = (octv << 4) | note          # reset with note applied
        dut.rst_n.value = 0
        await ClockCycles(dut.clk, 4)
        dut.rst_n.value = 1
        await RisingEdge(dut.clk)
        await ClockCycles(dut.clk, 4 * DIV)           # let the DSM/CORDIC settle

        # capture both 1-bit PDM outputs every clock: I on uo_out[7], Q on uio_out[7]
        bits_i = np.empty(N, dtype=np.int8)
        bits_q = np.empty(N, dtype=np.int8)
        for i in range(N):
            await RisingEdge(dut.clk)
            bits_i[i] = (int(dut.uo_out.value)  >> 7) & 1
            bits_q[i] = (int(dut.uio_out.value) >> 7) & 1
        x_i = bits_i.astype(float) * 2.0 - 1.0        # 0/1 -> -1/+1
        x_q = bits_q.astype(float) * 2.0 - 1.0
        y_i = np.convolve(x_i, h, mode="same")        # low-pass -> recovered sine
        y_q = np.convolve(x_q, h, mode="same")        # low-pass -> recovered cosine

        f_i, sfdr_i = band_metrics(y_i)
        f_q, _      = band_metrics(y_q)
        binw = CLK_HZ / N

        # quadrature: cross-correlate recovered I and Q over +-half a period. For an
        # ideal pair R(L) = -sin(2*pi*L/period): ~0 at zero lag, |peak| at a quarter
        # period (90 deg). The interior slice drops the FIR edge transients.
        period_clk = CLK_HZ / f_ideal
        assert N - 2 * NTAPS > 3 * period_clk, (      # xcorr needs several whole periods
            f"note {note} oct {octv}: capture N={N} too short for f={f_ideal:.0f} Hz; "
            f"use a higher octave or increase N")
        lags, R  = xcorr(y_i[NTAPS:N - NTAPS], y_q[NTAPS:N - NTAPS], int(period_clk // 2))
        r0       = float(R[np.searchsorted(lags, 0)])
        lag_peak = int(lags[np.argmax(np.abs(R))])
        quarter  = period_clk / 4.0

        dut._log.info(f"note {note} oct {octv} (f={f_ideal:7.1f}): "
                      f"I fpk={f_i:7.1f} SFDR={sfdr_i:5.1f}dB | Q fpk={f_q:7.1f} | "
                      f"xcorr r(0)={r0:+.3f} peak@{lag_peak:+d} (T/4={quarter:.0f})")

        # both tones land at f_ideal, the sine is clean, and the pair is in quadrature
        assert abs(f_i - f_ideal) < 2 * binw, \
            f"note {note} oct {octv}: I peak {f_i:.0f} Hz vs ideal {f_ideal:.0f} (bin {binw:.0f})"
        assert abs(f_q - f_ideal) < 2 * binw, \
            f"note {note} oct {octv}: Q peak {f_q:.0f} Hz vs ideal {f_ideal:.0f} (bin {binw:.0f})"
        assert sfdr_i > 30, f"note {note} oct {octv}: I SFDR {sfdr_i:.1f} dB"
        assert abs(r0) < 0.15, \
            f"note {note} oct {octv}: xcorr at zero lag {r0:+.3f} (expected ~0 for quadrature)"
        assert abs(abs(lag_peak) - quarter) < 0.15 * quarter, \
            f"note {note} oct {octv}: xcorr peak at {lag_peak:+d} clk, expected +-{quarter:.0f} (T/4)"

        if plot:
            fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(9, 10))
            # Zoom around a rising zero-crossing of recovered I, so the window is centred
            # on 0 and shows the PDM density sweeping through 0.5. Individual bits stay
            # visible instead of the full-rate blur.
            amp = np.abs(y_i).max()
            lo, hi = NTAPS, N - NTAPS                        # skip FIR edge transients
            sseg = y_i[lo:hi] - y_i[lo:hi].mean()
            zc  = np.where((sseg[:-1] <= 0) & (sseg[1:] > 0))[0]  # rising crossings
            idx = lo + int(zc[len(zc) // 2]) if len(zc) else (lo + hi) // 2
            a = max(lo, idx - 150); b = min(hi, idx + 150)   # ~300-clock window
            t = np.arange(a, b) - idx                        # x-axis relative to the crossing
            ax1.step(t, x_i[a:b], where="mid", alpha=0.4, label="raw PDM_I (±1)")
            ax1.plot(t, y_i[a:b] / amp, lw=1.8, color="C1", label="I low-pass (norm)")
            ax1.plot(t, y_q[a:b] / amp, lw=1.8, color="C2", label="Q low-pass (norm)")
            ax1.set(title=f"note {note} oct {octv}  f={f_ideal:.0f} Hz  (zoom @ I zero-crossing)",
                    xlabel="clock cycle (rel. to crossing)", ylabel="level"); ax1.legend(); ax1.grid(True)

            fr = np.fft.rfftfreq(N, d=1 / CLK_HZ); w = np.hanning(N)
            Sx = np.abs(np.fft.rfft((x_i - x_i.mean()) * w))
            Sy = np.abs(np.fft.rfft((y_i - y_i.mean()) * w))
            ref = Sx.max()
            ax2.semilogx(fr[1:], 20 * np.log10(Sx[1:] / ref + 1e-12), label="raw I", alpha=0.8)
            ax2.semilogx(fr[1:], 20 * np.log10(Sy[1:] / ref + 1e-12), label="filtered I")
            ax2.axvline(FC, color="k", ls=":", lw=0.9)
            ax2.axvline(f_ideal, color="r", ls="--", lw=0.8)
            ax2.set(xlabel="Hz", ylabel="dB", ylim=(-100, 5)); ax2.legend(); ax2.grid(True, which="both")

            ax3.plot(lags, R, lw=1.2)
            ax3.axhline(0, color="k", lw=0.6)
            ax3.axvline(0, color="r", ls="--", lw=0.8, label="zero lag (R~0)")
            ax3.axvline( quarter, color="g", ls=":", lw=0.9, label="±T/4 (peak)")
            ax3.axvline(-quarter, color="g", ls=":", lw=0.9)
            ax3.set(title="I/Q cross-correlation", xlabel="lag (clocks)", ylabel="R (norm)")
            ax3.legend(); ax3.grid(True)
            fig.tight_layout()
            fig.savefig(os.path.join(outdir, f"pdm_note{note}_oct{octv}.png"), dpi=90)
            plt.close(fig)