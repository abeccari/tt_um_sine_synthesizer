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
    inc  = SEMITONE[note if note < 12 else 0] << min(octv, MAX_OCT)

    # exact seamless loop: samples until the phase accumulator returns to 0
    L = (1 << N_ACC) // math.gcd(inc, 1 << N_ACC)
    if L > LMAX:
        dut._log.warning(f"loop period {L} samples > {LMAX}; capturing {LMAX} instead "
                         f"(low notes are not an exact loop -- the seam may tick)")
        L = LMAX
    f_out = inc * RATE / (1 << N_ACC)
    dut._log.info(f"note {note} oct {octv}: f={f_out:.1f} Hz, loop={L} samples "
                  f"= {L * DIV} clocks (~{L * DIV / CLK_HZ * 1e3:.0f} ms of sim)")

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

    # capture the 1-bit PDM (uo_out[7]) over exactly one loop period
    Nclk = L * DIV
    pdm  = np.empty(Nclk, dtype=np.int8)
    for i in range(Nclk):
        await RisingEdge(dut.clk)
        pdm[i] = (int(dut.uo_out.value) >> 7) & 1
    x = pdm.astype(float) * 2.0 - 1.0                 # 0/1 -> -1/+1

    # circular low-pass (loop is periodic -> no FIR edge transient) then decimate
    ntaps = 1023
    n = np.arange(ntaps) - (ntaps - 1) / 2
    h = np.sinc(2 * FC / CLK_HZ * n) * np.hamming(ntaps); h /= h.sum()
    hk = np.zeros(Nclk); hk[:ntaps] = h; hk = np.roll(hk, -(ntaps // 2))   # zero group delay
    y  = np.fft.irfft(np.fft.rfft(x) * np.fft.rfft(hk), n=Nclk)
    audio = y[::DIV][:L]                              # L samples @ 48 kHz, seamless

    # tile to the requested length, normalise, write 16-bit PCM
    reps  = int(np.ceil(secs * RATE / L))
    audio = np.tile(audio, reps)[:int(secs * RATE)]
    audio = audio - audio.mean()
    audio = audio / (np.abs(audio).max() + 1e-9)
    pcm   = (audio * 0.9 * 32767).astype("<i2")

    path = os.path.join(os.path.dirname(__file__), f"waves/tone_note{note}_oct{octv}.wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    dut._log.info(f"wrote {path}  ({secs:.1f}s, {RATE} Hz mono, {L} samples/loop)")
