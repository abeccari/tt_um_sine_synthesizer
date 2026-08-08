 # SPDX-FileCopyrightText: © 2026 Alberto Beccari
# SPDX-License-Identifier: Apache-2.0
"""Top-level cocotb tests for tt_um_abeccari_swsynth.

Pinout (see src/project.v):
  ui_in[3:0]   = NOTE          ui_in[7:4]   = OCTAVE
  uo_out[6:0]  = SINE (ob)     uo_out[7]    = PDM
  uio_out[0]   = SAMPLE_EN     uio_out[7:1] = COS (ob)
"""

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
