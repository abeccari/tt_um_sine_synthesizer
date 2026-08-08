# SPDX-FileCopyrightText: © 2026 Alberto Beccari
# SPDX-License-Identifier: Apache-2.0
"""Render a tone from the RTL PDM output to a .wav so you can *listen* to it.

Run on demand -- it is NOT part of the normal test suite (its own
COCOTB_TEST_MODULES), so `make` never pays for it:

    uv run make wav                                   # note 0 (A), octave 8, 3 s
    WAV_NOTE=7 WAV_OCT=8 WAV_SECONDS=5 uv run make wav

Rather than simulate several seconds of audio (tens of millions of clocks), it
captures exactly one *seamless-loop period*: the number of 48 kHz samples until
the phase accumulator returns to 0, i.e. an integer number of tone cycles. That
1-bit PDM loop is low-pass filtered (circularly, so the loop stays periodic) and
decimated to 48 kHz, then tiled to WAV_SECONDS. Because the loop is a whole
number of cycles, the tile seam has no discontinuity. Output: test/tone_note<N>_oct<O>.wav
"""
import math
import os
import wave

import numpy as np
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles

CLK_HZ   = 12_288_000
DIV      = 256
N_ACC    = 20
MAX_OCT  = 8
RATE     = CLK_HZ // DIV                 # 48000 Hz audio sample rate
FC       = 20_000                        # reconstruction low-pass cutoff
SEMITONE = [601, 636, 674, 714, 757, 802, 850, 900, 954, 1010, 1070, 1134]
LMAX     = 8192                          # cap on the captured loop (~2 M clocks)


@cocotb.test()
async def wav_export(dut):
    note = int(os.environ.get("WAV_NOTE", "0"))
    octv = int(os.environ.get("WAV_OCT", "8"))
    secs = float(os.environ.get("WAV_SECONDS", "3"))
    lpf  = os.environ.get("WAV_LPF", "1").lower() not in ("0", "false", "no", "off")
    inc  = SEMITONE[note if note < 12 else 0] << min(octv, MAX_OCT)

    P     = (1 << N_ACC) / inc                         # samples per tone period (float)
    f_out = inc * RATE / (1 << N_ACC)
    cap   = int(min(LMAX, max(4096, math.ceil(8 * P))))   # >= ~8 periods, bounded
    dut._log.info(f"note {note} oct {octv}: f={f_out:.1f} Hz, low-pass {'ON' if lpf else 'OFF'}, "
                  f"capturing {cap} samples = {cap * DIV} clocks "
                  f"(~{cap * DIV / CLK_HZ * 1e3:.0f} ms of sim)")

    # clock + reset with the note applied
    cocotb.start_soon(Clock(dut.clk, round(1e9 / CLK_HZ), unit="ns").start())
    dut.ena.value    = 1
    dut.uio_in.value = 0
    dut.ui_in.value  = (octv << 4) | note
    dut.rst_n.value  = 0
    await ClockCycles(dut.clk, 4)
    dut.rst_n.value  = 1
    await RisingEdge(dut.clk)
    await ClockCycles(dut.clk, 4 * DIV)               # settle

    # capture the 1-bit PDM (uo_out[7]) for the chunk
    Nclk = cap * DIV
    pdm  = np.empty(Nclk, dtype=np.int8)
    for i in range(Nclk):
        await RisingEdge(dut.clk)
        pdm[i] = (int(dut.uo_out.value) >> 7) & 1
    x = pdm.astype(float) * 2.0 - 1.0                 # 0/1 -> -1/+1

    # Reconstruct to 48 kHz. WITH the low-pass: a proper anti-aliased decimation
    # (clean tone, ~ what the RC filter / your ear delivers). WITHOUT it: naive
    # decimation, so the shaped ultrasonic noise folds into the audio band -- you
    # hear why the reconstruction filter is needed (harsher than the real chip,
    # whose ultrasonics your ear would simply not hear).
    if lpf:
        ntaps = 1023
        n = np.arange(ntaps) - (ntaps - 1) / 2
        h = np.sinc(2 * FC / CLK_HZ * n) * np.hamming(ntaps); h /= h.sum()
        y = np.convolve(x, h, mode="same")
        edge = ntaps // 2
        audio = y[edge:Nclk - edge][::DIV]
    else:
        audio = x[::DIV].astype(float)                # aliased on purpose

    # Click-free loop: the accumulator's exact repeat period is huge for low/mid
    # notes, so a plain tile would click. Trim to a whole number of *tone* periods,
    # then crossfade the tail into the head so the wrap point is continuous.
    k  = max(1, int(len(audio) / P))
    M  = min(len(audio), int(round(k * P)))
    a  = audio[:M].astype(float)
    xf = min(256, M // 8)
    w  = np.linspace(0.0, 1.0, xf)
    loop = a[:-xf].copy()
    loop[:xf] = a[-xf:] * (1.0 - w) + a[:xf] * w
    dut._log.info(f"loop = {len(loop)} samples ({k} tone periods), crossfade {xf} samples")

    # tile to the requested length, normalise, write 16-bit PCM
    reps  = int(np.ceil(secs * RATE / len(loop)))
    audio = np.tile(loop, reps)[:int(secs * RATE)]
    audio = audio - audio.mean()
    audio = audio / (np.abs(audio).max() + 1e-9)
    pcm   = (audio * 0.9 * 32767).astype("<i2")

    outdir = os.path.join(os.path.dirname(__file__), "waves")
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"tone_note{note}_oct{octv}_{'lpf' if lpf else 'raw'}.wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    dut._log.info(f"wrote {path}  ({secs:.1f}s, {RATE} Hz mono, {len(loop)} samples/loop)")
