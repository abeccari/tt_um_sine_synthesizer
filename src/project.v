/*
 * Copyright (c) 2026 Alberto Beccari
 * SPDX-License-Identifier: Apache-2.0
 *
 * tt_um_abeccari_swsynth -- CORDIC sine-wave synthesizer.
 *
 *   ui_in[3:0]   NOTE      : semitone within the octave (0=A .. 11=G#; 12-15 -> A)
 *   ui_in[7:4]   OCT       : octaves above A0 = 27.5 Hz (0-8, clamped below Nyquist)
 *   uo_out[6:0]  SINE      : 7-bit sine sample, OFFSET-BINARY (64 = zero-crossing)
 *   uo_out[7]    PDM       : 1-bit sigma-delta stream (TT Audio Pmod compatible)
 *   uio_out[0]   SAMPLE_EN : 48 kHz sample strobe (debug / scope trigger)
 *   uio_out[7:1] COS       : 7-bit cosine sample, OFFSET-BINARY (debug / quadrature)
 *   clk                    : 12.288 MHz  (= 256 * 48 kHz sample rate)
 *   rst_n                  : active-low reset
 *
 * Pipeline (build each block below):
 *   FREQ -> [2FF sync] -> [freq map] -> phase_inc
 *                                         |
 *   sample_en (clk/256) -> [phase accumulator N=24] -> phase code
 *                                         |
 *                          [CORDIC + quadrant fold] -> sin/cos (signed, W=12)
 *                                         |
 *                     +-------------------+-------------------+
 *                     v                                       v
 *        [round + saturate -> offset-binary]        [1st-order sigma-delta]
 *                     v                                       v
 *              sine_ob / cos_ob                            pdm_bit
 *
 * SCAFFOLD ONLY: the pin mapping is wired up; implement the numbered blocks.
 * Drive the four signals in the "signals you produce" section, and the outputs
 * fall into place. (Declared as wire -- change to reg if you drive them from an
 * always block.)
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
  localparam integer N_ACC = 20;  // phase-accumulator width (freq resolution)
  localparam integer SW    = 12;  // internal signed sample width (CORDIC datapath)
  localparam integer OW    = 7;   // parallel output width

  // ---------------------------------------------------------------------------
  // Signals
  // ---------------------------------------------------------------------------
  wire [OW-1:0] sine_ob;    // 7-bit sine,   offset-binary -> uo_out[6:0]
  wire          pdm_bit;    // 1-bit sigma-delta           -> uo_out[7]
  wire [OW-1:0] cos_ob;     // 7-bit cosine, offset-binary -> uio_out[7:1]
  wire          sample_en;  // 48 kHz sample strobe        -> uio_out[0]

  // ---------------------------------------------------------------------------
  // 1. Input synchroniser
  // ---------------------------------------------------------------------------

  /* Two flip-flops sync the input signal to clk and avoid metastability */

  reg [7:0] ui_sync0, ui_sync1;
  always @(posedge clk) begin
    ui_sync0 <= ui_in;
    ui_sync1 <= ui_sync0;
  end
  
  
  // ---------------------------------------------------------------------------
  // 2. Frequency map : ui_in {octave[7:4], note[3:0]} -> N_ACC-bit phase_inc
  // ---------------------------------------------------------------------------

  wire [N_ACC-1:0] phase_inc;

  freq_map #(.N(N_ACC)) u_freq_map (
    .freq_word(ui_sync1),
    .phase_inc(phase_inc)
  );


  // ---------------------------------------------------------------------------
  // 3. Phase accumulator (NCO) : phase_acc += phase_inc on sample_en, wraps mod 2^N
  // ---------------------------------------------------------------------------

  wire [N_ACC-1:0] phase_acc;

  nco #( .N(N_ACC) ) u_nco (
    .clk(clk),
    .rst_n(rst_n),
    .phase_inc(phase_inc),
    .sample_en(sample_en),
    .phase(phase_acc)
  );

  // ---------------------------------------------------------------------------
  // 4. CORDIC core (rotation mode) + quadrant fold : phase code -> signed sin/cos
  //    TODO
  // ---------------------------------------------------------------------------

  wire signed [SW-1:0] sine_s, cos_s; // signed CORDIC outputs, Q2.(SW-2)

  cordic #(.PW(N_ACC), .W(SW)) u_cordic (   // NITER defaults to 6
    .clk(clk),
    .rst_n(rst_n),
    .phase_acc(phase_acc),
    .sine(sine_s),
    .cosine(cos_s)
  );

  // ---------------------------------------------------------------------------
  // 5a. Output format : round + saturate signed SW -> OW, then to offset-binary.
  //     Drive sine_ob and cos_ob (64 = zero-crossing).
  // ---------------------------------------------------------------------------

  sample_to_ob #(.SW(SW), .OW(OW)) u_sine_fmt (.s(sine_s), .ob(sine_ob));
  sample_to_ob #(.SW(SW), .OW(OW)) u_cos_fmt  (.s(cos_s),  .ob(cos_ob));

  // ---------------------------------------------------------------------------
  // 5b. Sigma-delta : 1st-order modulator at full clk rate (OSR = 256), fed the
  //     FULL-precision sample (not the 7-bit value). Drive pdm_bit.
  // ---------------------------------------------------------------------------

  // Full-precision SW-bit sine (scaled to full swing) -> better PDM SNR than the 7-bit value.
  wire [SW-1:0] sine_dsm;
  sample_to_ob #(.SW(SW), .OW(SW)) u_sine_dsm (.s(sine_s), .ob(sine_dsm));

  sigma_delta #(.W(SW)) u_dsm (
      .clk(clk), .rst_n(rst_n), .x(sine_dsm), .pdm_bit(pdm_bit)
  );

  // ---------------------------------------------------------------------------
  // 6. Pin mapping
  // ---------------------------------------------------------------------------
  assign uo_out  = {pdm_bit, sine_ob};    // [7]=PDM, [6:0]=sine (offset-binary)
  assign uio_out = {cos_ob, sample_en};   // [7:1]=cosine, [0]=SAMPLE_EN
  assign uio_oe  = 8'hFF;                  // all bidir pins driven as outputs

  // List all unused inputs to prevent warnings.
  wire _unused = &{ena, uio_in, 1'b0};

endmodule

// Frequency map: 8-bit word {octave[7:4], note[3:0]} -> phase increment.
//   note[3:0] : equal-tempered semitone, 0=A .. 11=G# (codes 12-15 fold to A)
//   oct [7:4] : whole-octave shift (doubling phase_inc = +1 octave, since f prop phase_inc)
// LUT values are for A0 = 27.5 Hz, f_s = 48 kHz, N = 20 accumulator.

module freq_map #(
  parameter integer N       = 20,
  parameter integer MAX_OCT = 8    // clamp: keeps the top note (G#) below Nyquist (2^(N-1))
) (
  input  wire [7:0]   freq_word,   // {octave[7:4], note[3:0]}
  output wire [N-1:0] phase_inc
);
  wire [3:0] note = freq_word[3:0];
  wire [3:0] oct  = freq_word[7:4];

  // One octave of equal-tempered semitones from A0 = 27.5 Hz.
  reg [N-1:0] base;
  always @(*) begin
    case (note)
      4'd0:    base = 'd601;   // A    27.50 Hz
      4'd1:    base = 'd636;   // A#   29.14 Hz
      4'd2:    base = 'd674;   // B    30.87 Hz
      4'd3:    base = 'd714;   // C    32.70 Hz
      4'd4:    base = 'd757;   // C#   34.65 Hz
      4'd5:    base = 'd802;   // D    36.71 Hz
      4'd6:    base = 'd850;   // D#   38.89 Hz
      4'd7:    base = 'd900;   // E    41.20 Hz
      4'd8:    base = 'd954;   // F    43.65 Hz
      4'd9:    base = 'd1010;  // F#   46.25 Hz
      4'd10:   base = 'd1070;  // G    49.00 Hz
      4'd11:   base = 'd1134;  // G#   51.91 Hz
      default: base = 'd601;   // codes 12-15 -> A
    endcase
  end

  // Octave = left shift; clamp so the tone stays below Nyquist (no aliasing).
  wire [3:0] oct_cap = (oct > MAX_OCT[3:0]) ? MAX_OCT[3:0] : oct;
  assign phase_inc = base << oct_cap;

endmodule

// Sigma-delta modulator module definition (1st-order)

module sigma_delta #(
    parameter integer W = 12
) (
    input  wire         clk,
    input  wire         rst_n,
    input  wire [W-1:0] x,        // full-precision UNSIGNED (offset-binary) sample
    output reg          pdm_bit
);
    reg [W-1:0] dsm_acc;          // internal state = the error residue
    always @(posedge clk) begin
        if (!rst_n) begin
            dsm_acc <= {W{1'b0}};
            pdm_bit <= 1'b0;
        end
        else begin
            {pdm_bit, dsm_acc} <= dsm_acc + x;
        end
    end
endmodule

// Phase accumulator and sample rate oscillator

module nco #(
  parameter integer N = 20 // phase increment width
  )(
  input wire clk,
  input wire rst_n,
  input wire [N-1:0] phase_inc,
  output wire sample_en,
  output reg [N-1:0] phase
);

  reg [7:0] div;
  always @(posedge clk)
    if (!rst_n) div <= 8'b0;
    else div <= div + 8'b1;
  assign sample_en = (div == 8'hff); // High for exactly one clock cycle every 256

  always @(posedge clk)
    if (!rst_n) phase <= 'b0;
    else if (sample_en) phase <= phase + phase_inc;

endmodule

// CORDIC engine (rotation mode) + quadrant fold: phase code -> signed sin/cos.
// Datapath width W is parametric (Q2.(W-2)). The atan table is stored once at
// WMAX-bit precision and rescaled to W bits by a constant shift (fully synthesizable),
// so changing W is a one-line edit. Master table computed offline (turns * 2^WMAX).

module cordic #(
  parameter integer PW    = 20,   // phase accumulator width (full turn = 2^PW)
  parameter integer W     = 12,   // datapath width, Q2.(W-2)
  parameter integer NITER = 6     // rotation stages (~7 effective bits at W=12)
)(
  input  wire                clk,
  input  wire                rst_n,
  input  wire [PW-1:0]       phase_acc,
  output reg  signed [W-1:0] sine,
  output reg  signed [W-1:0] cosine
);
  localparam integer    WMAX   = 16;         // master precision (require W <= WMAX)
  localparam integer    SH     = WMAX - W;
  localparam [WMAX-1:0] GAIN_M = 16'd9949;   // 1/K in Q2.14

  // master atan table: round( atan(2^-k)/(2*pi) * 2^WMAX )
  function [WMAX-1:0] atan_m(input integer k);
    case (k)
      0:atan_m=16'd8192; 1:atan_m=16'd4836; 2:atan_m=16'd2555; 3:atan_m=16'd1297;
      4:atan_m=16'd651;  5:atan_m=16'd326;  6:atan_m=16'd163;  7:atan_m=16'd81;
      8:atan_m=16'd41;   9:atan_m=16'd20;  10:atan_m=16'd10;  11:atan_m=16'd5;
      12:atan_m=16'd3;  13:atan_m=16'd1;  14:atan_m=16'd1;    default: atan_m=16'd0;
    endcase
  endfunction

  // rescale a WMAX-bit constant down to W bits (rounded); constant-folds at elaboration
  function signed [W-1:0] rescale(input [WMAX-1:0] m);
    rescale = (SH == 0) ? m[W-1:0] : ((m + (1 << (SH-1))) >> SH);
  endfunction

  reg signed [W-1:0] x, xn, y, z, c_full, s_full;
  reg [1:0] quad;
  integer k;

  always @(*) begin
    quad = phase_acc[PW-1 -: 2];            // top 2 bits -> quadrant
    z    = phase_acc[PW-3 -: (W-2)];        // next W-2 bits -> in-quadrant residual
    x    = rescale(GAIN_M);                 // 1/K gain seed
    y    = '0;
    for (k = 0; k < NITER; k = k + 1) begin
      if (z >= 0) begin
        xn = x - (y >>> k); y = y + (x >>> k); x = xn; z = z - rescale(atan_m(k));
      end else begin
        xn = x + (y >>> k); y = y - (x >>> k); x = xn; z = z + rescale(atan_m(k));
      end
    end
    case (quad)                             // lift first quadrant to the full circle
      2'd0: begin c_full =  x; s_full =  y; end
      2'd1: begin c_full = -y; s_full =  x; end
      2'd2: begin c_full = -x; s_full = -y; end
      2'd3: begin c_full =  y; s_full = -x; end
    endcase
  end

  always @(posedge clk) begin               // register the result
    if (!rst_n) begin sine <= '0; cosine <= '0; end
    else        begin sine <= s_full; cosine <= c_full; end
  end
endmodule

// Format a signed Q2.(SW-2) sample to OW-bit offset-binary:
// scale amplitude 1.0 (=2^(SW-2)) to near full OW-scale, round, saturate, flip MSB.
// SHIFT = (SW-2) frac bits -> (OW-1) frac bits  = SW-OW-1.
module sample_to_ob #(
  parameter integer SW    = 12,
  parameter integer OW    = 7,
  parameter integer SHIFT = SW - OW - 1
) (
  input  wire signed [SW-1:0] s,
  output wire        [OW-1:0] ob
);
  // SHIFT>=0 narrows (round + right shift); SHIFT<0 widens (left shift, scale up).
  // Constants are declared signed so the arithmetic stays signed -- a bare unsigned
  // literal would make the whole expression unsigned and turn >>> into a logical shift.
  localparam integer       RSH = (SHIFT > 0) ? SHIFT  : 0;   // narrow: right shift amount
  localparam integer       LSH = (SHIFT < 0) ? -SHIFT : 0;   // widen : left shift amount
  localparam signed [SW:0] RND = (SHIFT > 0) ? (1 <<< (SHIFT-1)) : 0;   // +1/2 LSB
  localparam signed [OW:0] HI  =  (1 <<< (OW-1)) - 1;                   // +max
  localparam signed [OW:0] LO  = -(1 <<< (OW-1));                       // -min

  wire signed [SW+LSH:0] s_sc = (($signed(s) + RND) >>> RSH) <<< LSH;   // round + rescale
  reg  signed [OW-1:0] s_sat;
  always @(*) begin
    if      (s_sc > HI) s_sat = HI[OW-1:0];          // saturate high
    else if (s_sc < LO) s_sat = LO[OW-1:0];          // saturate low
    else                s_sat = s_sc[OW-1:0];
  end
  assign ob = {~s_sat[OW-1], s_sat[OW-2:0]};         // signed -> offset-binary (mid = 2^(OW-1))
endmodule