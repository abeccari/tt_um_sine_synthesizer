/*
 * Copyright (c) 2026 Alberto Beccari
 * SPDX-License-Identifier: Apache-2.0
 *
 * tt_um_abeccari_swsynth -- CORDIC sine-wave synthesizer.
 *
 *   ui_in[7:0]  FREQ    : 8-bit frequency word (exponential map, 10 Hz - 20 kHz)
 *   uo_out[6:0] SINE    : 7-bit sine sample, OFFSET-BINARY (64 = zero-crossing)
 *   uo_out[7]   PDM     : 1-bit sigma-delta stream (TT Audio Pmod compatible)
 *   clk                 : 12.288 MHz  (= 256 * 48 kHz sample rate)
 *   rst_n               : active-low reset
 *
 * Pipeline:
 *   FREQ -> [2FF sync] -> [freq map] -> phase_inc
 *                                         |
 *   sample_en (clk/256) -> [phase accumulator N=24] -> phase code
 *                                         |
 *                          [CORDIC + quadrant fold] -> sample_s (signed, W=12)
 *                                         |
 *                     +-------------------+-------------------+
 *                     v                                       v
 *        [round + saturate -> offset-binary]        [1st-order sigma-delta]
 *                     v                                       v
 *                 uo_out[6:0]                             uo_out[7]
 *
 * Blocks marked TODO are stubs to be filled in next.
 */

`default_nettype none

module tt_um_abeccari_swsynth (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // IOs: Input path
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  wire       ena,      // always 1 when the design is powered, so you can ignore it
    input  wire       clk,      // clock (12.288 MHz)
    input  wire       rst_n     // reset_n - low to reset
);

  // ---------------------------------------------------------------------------
  // Parameters
  // ---------------------------------------------------------------------------
  localparam integer N_ACC = 24;  // phase-accumulator width (freq resolution)
  localparam integer SW    = 12;  // internal signed sample width (CORDIC datapath)
  localparam integer OW    = 7;   // parallel output width
  localparam integer DROP  = SW - OW;  // bits dropped when rounding SW -> OW

  // ---------------------------------------------------------------------------
  // 1. Input synchroniser -- ui_in is asynchronous to clk (2-FF sync)
  // ---------------------------------------------------------------------------
  reg [7:0] freq_sync0, freq_word;
  always @(posedge clk) begin
    if (!rst_n) begin
      freq_sync0 <= 8'd0;
      freq_word  <= 8'd0;
    end else begin
      freq_sync0 <= ui_in;
      freq_word  <= freq_sync0;
    end
  end

  // ---------------------------------------------------------------------------
  // 2. Frequency map : 8-bit word -> N-bit phase increment
  //    TODO: replace linear placeholder with the exponential law
  //          (256-entry ROM for bit-exact, or float-decode for min area).
  // ---------------------------------------------------------------------------
  wire [N_ACC-1:0] phase_inc = {{(N_ACC-8){1'b0}}, freq_word} << 4;  // PLACEHOLDER

  // ---------------------------------------------------------------------------
  // 3. Sample-rate strobe : sample_en pulses at clk/256 = 48 kHz
  // ---------------------------------------------------------------------------
  reg  [7:0] strobe_cnt;
  wire       sample_en = (strobe_cnt == 8'd0);
  always @(posedge clk) begin
    if (!rst_n) strobe_cnt <= 8'd0;
    else        strobe_cnt <= strobe_cnt + 8'd1;
  end

  // ---------------------------------------------------------------------------
  // 4. Phase accumulator (NCO) -- wraps mod 2^N automatically
  // ---------------------------------------------------------------------------
  reg [N_ACC-1:0] phase_acc;
  always @(posedge clk) begin
    if (!rst_n)          phase_acc <= {N_ACC{1'b0}};
    else if (sample_en)  phase_acc <= phase_acc + phase_inc;
  end

  // ---------------------------------------------------------------------------
  // 5. Phase -> sine sample
  //    TODO: CORDIC core (rotation mode) + quadrant fold, driven from the top
  //          bits of phase_acc. Output is signed, full-scale ~ +/-2^(SW-1).
  //          Until then, hold silence (offset-binary midscale).
  // ---------------------------------------------------------------------------
  wire signed [SW-1:0] sample_s = {SW{1'b0}};  // PLACEHOLDER (0 -> midscale out)

  // ---------------------------------------------------------------------------
  // 6a. Parallel output : round + saturate signed SW -> signed OW, then
  //     convert to offset-binary (invert MSB). 64 = zero-crossing.
  // ---------------------------------------------------------------------------
  wire signed [SW:0] sample_ext   = {sample_s[SW-1], sample_s};
  wire signed [SW:0] sample_round = sample_ext + $signed(2 ** (DROP - 1));  // +1/2 LSB
  wire signed [SW:0] sample_shr   = sample_round >>> DROP;

  reg signed [OW-1:0] sample_sat;
  always @(*) begin
    if      (sample_shr >  63) sample_sat =  7'sd63;   // saturate high
    else if (sample_shr < -64) sample_sat = -7'sd64;   // saturate low
    else                       sample_sat = sample_shr[OW-1:0];
  end

  wire [OW-1:0] sine_ob = {~sample_sat[OW-1], sample_sat[OW-2:0]};  // signed -> offset-binary

  // ---------------------------------------------------------------------------
  // 6b. Sigma-delta output : 1st-order modulator at full clk rate (OSR = 256).
  //     Fed the FULL-precision sample (not the truncated 7-bit value).
  //     TODO: consider 2nd-order for better in-band SNR.
  // ---------------------------------------------------------------------------
  wire [SW-1:0] sample_u = {~sample_s[SW-1], sample_s[SW-2:0]};  // full-width offset-binary
  reg  [SW:0]   dsm_acc;
  always @(posedge clk) begin
    if (!rst_n) dsm_acc <= {(SW+1){1'b0}};
    else        dsm_acc <= dsm_acc[SW-1:0] + sample_u;  // carry-out = PDM bit
  end
  wire pdm_bit = dsm_acc[SW];

  // ---------------------------------------------------------------------------
  // 7. Pin assignments
  // ---------------------------------------------------------------------------
  assign uo_out  = {pdm_bit, sine_ob};  // [7]=PDM, [6:0]=sine (offset-binary)
  assign uio_out = 8'b0;                // bidir bus unused -> tie off
  assign uio_oe  = 8'b0;                // all bidir pins as inputs

  // List all unused inputs to prevent warnings. phase_acc is consumed once the
  // CORDIC block (step 5) is wired in; kept here until then.
  wire _unused = &{ena, uio_in, phase_acc, 1'b0};

endmodule
