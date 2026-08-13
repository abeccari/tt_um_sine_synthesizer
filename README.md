[![gds](https://github.com/abeccari/tt_um_sine_synthesizer/actions/workflows/gds.yaml/badge.svg)](https://github.com/abeccari/tt_um_sine_synthesizer/actions/workflows/gds.yaml)
[![docs](https://github.com/abeccari/tt_um_sine_synthesizer/actions/workflows/docs.yaml/badge.svg)](https://github.com/abeccari/tt_um_sine_synthesizer/actions/workflows/docs.yaml)
[![test](https://github.com/abeccari/tt_um_sine_synthesizer/actions/workflows/test.yaml/badge.svg)](https://github.com/abeccari/tt_um_sine_synthesizer/actions/workflows/test.yaml)

# Sine Wave Synthesizer — Tiny Tapeout IHP 26b

[![Made with Claude](https://img.shields.io/badge/Made%20with-Claude-D97757?logo=anthropic&logoColor=white)](https://claude.com/claude-code)

A **CORDIC sine-wave synthesizer** for the Tiny Tapeout IHP 26b shuttle (IHP SG13G2, 130 nm). It plays an equal-tempered musical note as an audio tone, selected from the input pins, and emits it three ways: a 7-bit parallel sample bus and two 1-bit pulse-density-modulated (PDM) streams — sine and cosine, 90° apart — for direct analog reconstruction and I/Q experiments.

- [Datasheet page](docs/info.md)

## How it works

The design is a numerically-controlled oscillator feeding a CORDIC rotator:

```
NOTE / OCT -> [2FF sync] -> [freq map] -> phase_inc
                                            |
        sample_en (clk/256) -> [phase accumulator, N=20] -> phase
                                            |
                        [CORDIC rotation + quadrant fold] -> sin, cos  (signed, W=12)
                                            |
               +----------------------------+----------------------------+
               v                            v                            v
      [round -> offset-binary]    [sine -> sigma-delta]        [cos -> sigma-delta]
               v                            v                            v
         SINE (uo[6:0])              PDM_I (uo[7])                PDM_Q (uio[7])
```

- **Frequency map** turns note/octave into a phase increment: `f = 27.5 · 2^(note/12) · 2^octave` Hz, octave clamped to ≤ 8 to stay below Nyquist.
- **Phase accumulator** (20-bit) advances once per 48 kHz sample (`clk / 256`); `SAMPLE_EN` marks each new sample.
- **CORDIC** (rotation mode, 8 iterations, 12-bit datapath) produces signed sine and cosine, ~8-bit accurate.
- **Outputs**: the sine is formatted to 7-bit offset-binary; the full-precision sine and cosine each drive a first-order sigma-delta modulator running at the clock rate.

Full detail, test recipe and external-hardware options are on the [datasheet](docs/info.md).

## Pin map

| Signal | Pins | Function |
|---|---|---|
| `NOTE` | `ui_in[3:0]` | semitone, 0 = A … 11 = G# (codes 12–15 fold to A) |
| `OCT` | `ui_in[7:4]` | octave above A0 = 27.5 Hz (clamped to ≤ 8) |
| `SINE` | `uo_out[6:0]` | 7-bit sine sample, offset-binary (`0x40` = zero crossing) |
| `PDM_I` | `uo_out[7]` | 1-bit sigma-delta of the sine (clock rate) |
| `SAMPLE_EN` | `uio_out[0]` | 48 kHz sample strobe, one clock wide |
| `PDM_Q` | `uio_out[7]` | 1-bit sigma-delta of the cosine (90° from PDM_I) |
| `SQR` | `uio_out[6]` | 1-bit square wave at the tone frequency (sign of the sine) |
| `SAW` | `uio_out[5]` | 1-bit sigma-delta sawtooth at the tone frequency (raw phase ramp) |
| — | `uio_out[4:1]` | unused, driven 0 |

All `uio` pins are outputs (`uio_oe = 0xFF`). Clock is 12.288 MHz = 256 × 48 kHz; `rst_n` is active-low. The note can change at any time and the tone follows on the next samples.

## Results

**Hardening** (LibreLane, IHP SG13G2, 1×1 tile, 20 ns / 50 MHz target):

| | |
|---|---|
| Utilisation | 59.4% |
| Standard cells | 1373 (excl. fill/tap) |
| Flip-flops | 85 |
| Inferred latches | none |
| Lint | 0 errors, 7 warnings (benign width-extend) |
| DRC / LVS / antenna | clean (magic DRC 0, LVS 0, antenna 0) |
| Power (typ) | ~0.36 mW |

**Timing** — closed with **zero setup and hold violations at all three corners** (20 ns period):

| Corner | Setup slack | Critical path | Implied f_max |
|---|---|---|---|
| slow, 1.08 V, 125 °C | 4.92 ns | 15.08 ns | **≈ 66 MHz** |
| typical, 1.20 V, 25 °C | 10.32 ns | 9.68 ns | ≈ 103 MHz |
| fast, 1.32 V, −40 °C | 13.56 ns | 6.44 ns | ≈ 155 MHz |

`f_max = 1 / (period − setup_slack)` and is a floor, not a ceiling: the flow hardens at a fixed 20 ns and stops optimising once slack is met. The worst-case path is the internal CORDIC datapath (`phase_acc → arithmetic → register`), register-to-register. In practice the silicon is good from DC up to ~60 MHz within the process corners; the demo board's on-board clock tops out at 50 MHz, met with ~4.9 ns of margin. The intended operating point is 12.288 MHz.

## Golden model and tests

**Golden model.** [`docs/cordic_sample.py`](docs/cordic_sample.py) is a self-contained, bit-accurate Python model of the CORDIC core — the same Q2.(W−2) fixed point, `atan` table, quadrant folding and arithmetic shifts as the RTL `cordic` module. It was used to develop and cross-check the rotation algorithm and to choose the iteration count from its convergence curve (RMS error vs. number of iterations). Run it standalone to see the approximation against ground truth and the convergence plot:

```bash
uv run --extra plot python docs/cordic_sample.py
```

**cocotb tests.** [`test/test.py`](test/test.py) drives the assembled design and checks its behaviour — spectral and quadrature properties rather than bit-exact samples, since the modulators and output formatting sit downstream of the CORDIC:

| Test | Property | Breaks if |
|---|---|---|
| `test_reset` | reset clears the accumulator and outputs; `SAMPLE_EN` stays low; resumes on release | reset wiring wrong |
| `test_sample_en` | `SAMPLE_EN` is a 1-clock pulse every 256 clocks (48 kHz) | divider miscounts |
| `test_nco_period` | phase wraps at the mapped frequency across octaves and notes; within 5 cents of ideal | freq map / accumulator wrong |
| `test_sine` | parallel sine: DC ≈ 0, correct amplitude, spectral peak at `f_ideal`, SFDR > 25 dB | CORDIC or formatting wrong |
| `test_pdm_audio` | both PDM streams reconstruct to `f_ideal`, sine SFDR > 30 dB, and I/Q are in quadrature (cross-correlation ≈ 0 at zero lag, extremal at a quarter period) | modulator or quadrature wrong |

`test_reset` and `test_nco_period` are whitebox (they read internal nets) and auto-skip under gate-level simulation (`GATES=yes`), where those nets no longer exist in the netlist.

## Running the tests

Toolchain: **uv** manages the Python dependencies (cocotb 2.0.1, pytest, numpy; matplotlib is an optional `plot` extra), and **Icarus Verilog** (Homebrew, v14) is the simulator. Keep oss-cad-suite off `PATH` for the RTL tests.

```bash
# from the repo root
cd test

# full RTL cocotb suite
uv run make

# a single test
COCOTB_TESTCASE=test_pdm_audio uv run make

# with waveform PNGs written to test/waves/ (needs the plot extra)
PLOT_WAVES=1 uv run --extra plot make

# gate-level simulation (after a GDS build drops in gate_level_netlist.v)
GATES=yes uv run make
```

Listen to a tone — an on-demand render to a `.wav`, not part of the suite:

```bash
uv run make wav                              # note A, octave 8, 3 s
WAV_NOTE=7 WAV_OCT=8 WAV_SECONDS=5 uv run make wav
WAV_LPF=0 uv run make wav                    # skip the reconstruction filter (aliased, for A/B)
```

## Repository layout

```
├── README.md                 # this file
├── info.yaml                 # Tiny Tapeout metadata, 1x1 tile
├── pyproject.toml            # uv project: cocotb, pytest, numpy, [plot] matplotlib
├── .github/workflows/        # gds, docs, test, fpga
├── docs/
│   ├── info.md               # datasheet page
│   ├── cordic_sample.py      # bit-accurate CORDIC golden model
│   ├── note4_oct8.png        # parallel sine waveform + spectrum
│   └── pdm_note0_oct8.png    # PDM I/Q reconstruction + cross-correlation
├── src/
│   └── project.v             # top level + freq_map, nco, cordic, sigma_delta, sample_to_ob
└── test/
    ├── Makefile              # cocotb + Icarus Verilog; also the `wav` target
    ├── requirements.txt      # pinned cocotb, pytest, numpy
    ├── tb.v                  # testbench wrapper
    ├── test.py               # the tests above
    └── wav_export.py         # on-demand .wav renderer
```

## License

Apache-2.0. Built from the [Tiny Tapeout IHP Verilog template](https://github.com/TinyTapeout/ttihp-verilog-template).
