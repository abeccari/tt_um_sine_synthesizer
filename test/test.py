 # SPDX-FileCopyrightText: © 2026 Alberto Beccari
# SPDX-License-Identifier: Apache-2.0
"""Top-level cocotb tests for tt_um_abeccari_swsynth.

Pinout (see src/project.v):
  ui_in[3:0]   = NOTE          ui_in[7:4]   = OCTAVE
  uo_out[6:0]  = SINE (ob)     uo_out[7]    = PDM
  uio_out[0]   = SAMPLE_EN     uio_out[7:1] = COS (ob)
"""

import math

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles

CLK_HZ = 12_288_000            # 12.288 MHz system clock
CLK_PS = round(1e9 / CLK_HZ)  # clock period in ns (~81.380)
DIV    = 256                   # sample-rate divider -> f_s = clk / 256 = 48 kHz


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

@cocotb.test()
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
    assert int(dut.uio_out.value[7:1]) == 64,          "COS not at midscale in reset"

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
    while the other uio_out bits (cosine) are still resolving after reset."""
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


@cocotb.test()
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


async def capture_sine_cos(dut, n):
    """Collect n signed output samples (one per SAMPLE_EN).
    SINE = uo_out[6:0], COS = uio_out[7:1], both offset-binary (midscale 64)."""
    sine, cos = [], []
    for _ in range(n):
        while str(dut.uio_out.value)[-1] != "1":     # wait for SAMPLE_EN
            await RisingEdge(dut.clk)
        await ClockCycles(dut.clk, 2)                # let accumulate + CORDIC reg settle
        sine.append((int(dut.uo_out.value) & 0x7F) - 64)
        cos.append(((int(dut.uio_out.value) >> 1) & 0x7F) - 64)
    return sine, cos


@cocotb.test()
async def test_sine_cos(dut):
    """Capture sine/cos, check spectrum @ f_ideal, amplitude, purity, quadrature."""
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

    cases = ((0, 3), (8, 6), (4, 8), (15, 15))

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
        sine, cos = await capture_sine_cos(dut, Ns + 4)
        s = np.array(sine[4:], float)                    # drop reset transient
        c = np.array(cos[4:], float)
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

        r      = np.sqrt(s ** 2 + c ** 2)
        ripple = r.std() / r.mean()
        assert ripple < 0.03, f"note {note} oct {octv}: sin^2+cos^2 ripple {ripple*100:.1f}%"

        dut._log.info(f"note {note:2d} oct {octv}: f_pk={f_pk:8.1f} Hz (ideal {f_ideal:8.1f}) "
                      f"amp={amp:.0f} SFDR={sfdr:.1f}dB ripple={ripple*100:.1f}%")

        # waveform + spectrum for visual inspection (opt-in via PLOT_WAVES)
        if plot:
            fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(8, 9))
            show = min(len(s), int(3 * period) + 2)
            ax1.plot(s[:show], ".-", label="sine"); ax1.plot(c[:show], ".-", label="cos")
            ax1.set(title=f"note {note} oct {octv} (f={f_ideal:.1f} Hz)",
                    xlabel="sample", ylabel="code"); ax1.legend(); ax1.grid(True)
            ax2.plot(freq, 20 * np.log10(spec / spec.max() + 1e-12))
            ax2.axvline(f_ideal, color="r", ls="--", lw=0.8)
            ax2.set(xlabel="Hz", ylabel="dB", ylim=(-80, 5)); ax2.grid(True)
            r2 = s ** 2 + c ** 2                             # CORDIC magnitude^2 (should be flat)
            ax3.plot(r2, ".-", lw=0.8)
            ax3.axhline(r2.mean(), color="r", ls="--", lw=0.8, label=f"mean {r2.mean():.0f}")
            ax3.set(title="sin² + cos²  (CORDIC magnitude²)", xlabel="sample",
                    ylabel="code²"); ax3.legend(); ax3.grid(True)
            fig.tight_layout(); fig.savefig(os.path.join(outdir, f"note{note}_oct{octv}.png"), dpi=90)
            plt.close(fig)


@cocotb.test()
async def test_pdm_audio(dut):
    """Capture the 1-bit PDM stream (uo_out[7]) at the full clock, low-pass it at
    24 kHz, and check the recovered tone: centre frequency and in-band SFDR.
    (Amplitude is arbitrary after filtering, so it's not asserted.)"""
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

        # capture the 1-bit PDM output on every clock
        bits = np.empty(N, dtype=np.int8)
        for i in range(N):
            await RisingEdge(dut.clk)
            bits[i] = (int(dut.uo_out.value) >> 7) & 1
        x = bits.astype(float) * 2.0 - 1.0            # 0/1 -> -1/+1
        y = np.convolve(x, h, mode="same")            # low-pass -> recovered tone

        f_pre,  sfdr_pre  = band_metrics(x)
        f_post, sfdr_post = band_metrics(y)
        binw = CLK_HZ / N

        dut._log.info(f"note {note} oct {octv} (f={f_ideal:7.1f}): "
                      f"raw fpk={f_pre:7.1f} SFDR={sfdr_pre:5.1f}dB | "
                      f"filt fpk={f_post:7.1f} SFDR={sfdr_post:5.1f}dB")

        # checks on the FILTERED signal
        assert abs(f_post - f_ideal) < 2 * binw, (
            f"note {note} oct {octv}: filtered peak {f_post:.0f} Hz vs ideal "
            f"{f_ideal:.0f} (bin {binw:.0f})"
        )
        assert sfdr_post > 30, f"note {note} oct {octv}: filtered SFDR {sfdr_post:.1f} dB"

        if plot:
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 7))
            # Zoom around a rising zero-crossing of the recovered sine, so the window is
            # centred on 0 and shows the PDM density sweeping through 0.5. Individual
            # bits stay visible instead of the full-rate blur.
            amp = np.abs(y).max()
            lo, hi = NTAPS, N - NTAPS                        # skip FIR edge transients
            seg = y[lo:hi] - y[lo:hi].mean()
            zc  = np.where((seg[:-1] <= 0) & (seg[1:] > 0))[0]   # rising crossings
            idx = lo + int(zc[len(zc) // 2]) if len(zc) else (lo + hi) // 2
            a = max(lo, idx - 150); b = min(hi, idx + 150)   # ~300-clock window
            t = np.arange(a, b) - idx                        # x-axis relative to the crossing
            ax1.step(t, x[a:b], where="mid", alpha=0.5, label="raw PDM (±1)")
            ax1.plot(t, y[a:b] / amp, lw=1.8, color="C1", label="low-pass (normalised)")
            ax1.set(title=f"note {note} oct {octv}  f={f_ideal:.0f} Hz  (zoom @ zero-crossing)",
                    xlabel="clock cycle (rel. to crossing)", ylabel="level"); ax1.legend(); ax1.grid(True)

            fr = np.fft.rfftfreq(N, d=1 / CLK_HZ); w = np.hanning(N)
            Sx = np.abs(np.fft.rfft((x - x.mean()) * w))
            Sy = np.abs(np.fft.rfft((y - y.mean()) * w))
            ref = Sx.max()
            ax2.semilogx(fr[1:], 20 * np.log10(Sx[1:] / ref + 1e-12), label="raw", alpha=0.8)
            ax2.semilogx(fr[1:], 20 * np.log10(Sy[1:] / ref + 1e-12), label="filtered")
            ax2.axvline(FC, color="k", ls=":", lw=0.9)
            ax2.axvline(f_ideal, color="r", ls="--", lw=0.8)
            ax2.set(xlabel="Hz", ylabel="dB", ylim=(-100, 5)); ax2.legend(); ax2.grid(True, which="both")
            fig.tight_layout()
            fig.savefig(os.path.join(outdir, f"pdm_note{note}_oct{octv}.png"), dpi=90)
            plt.close(fig)
    pass
        