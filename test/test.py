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
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 6))
            show = min(len(s), int(3 * period) + 2)
            ax1.plot(s[:show], ".-", label="sine"); ax1.plot(c[:show], ".-", label="cos")
            ax1.set(title=f"note {note} oct {octv} (f={f_ideal:.1f} Hz)",
                    xlabel="sample", ylabel="code"); ax1.legend(); ax1.grid(True)
            ax2.plot(freq, 20 * np.log10(spec / spec.max() + 1e-12))
            ax2.axvline(f_ideal, color="r", ls="--", lw=0.8)
            ax2.set(xlabel="Hz", ylabel="dB", ylim=(-80, 5)); ax2.grid(True)
            fig.tight_layout(); fig.savefig(os.path.join(outdir, f"note{note}_oct{octv}.png"), dpi=90)
            plt.close(fig)


@cocotb.test()
async def test_pdm_audio(dut):
    pass    # TODO: low-pass the PDM stream (uo_out[7]) and check it reconstructs the sine
    pass
        