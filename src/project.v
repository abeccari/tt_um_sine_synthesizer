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

  wire [OW-1:0] sine_s, cos_s; // Signed outputs from CORDIC

  cordic #(.N_ACC(N_ACC)) u_cordic (
    .clk(sample_en),
    .rst_n(rst_n),
    .phase_acc(phase_acc),
    .sine(sine_s),
    .cosine(cos_s)
  );

  // ---------------------------------------------------------------------------
  // 5a. Output format : round + saturate signed SW -> OW, then to offset-binary.
  //     Drive sine_ob and cos_ob (64 = zero-crossing).
  // ---------------------------------------------------------------------------

  assign sine_ob = {~sine_s[OW-1], sine_s[OW-2:0]};
  assign cos_ob  = {~cos_s[OW-1], cos_s[OW-2:0]};

  // ---------------------------------------------------------------------------
  // 5b. Sigma-delta : 1st-order modulator at full clk rate (OSR = 256), fed the
  //     FULL-precision sample (not the 7-bit value). Drive pdm_bit.
  // ---------------------------------------------------------------------------

  sigma_delta #(.W(OW)) u_dsm (
      .clk(clk), .rst_n(rst_n), .x(sine_ob), .pdm_bit(pdm_bit)
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

// CORDIC engine: Compute sines and cosines by iterating additions and bit shifts.
// Uses the first input bits to determine the quadrant and swap signs and outputs if necessary.

module cordic #(
  parameter integer N_ACC = 20, // phase accumulator with (full turn = 2 ** N_ACC)
  parameter integer N_ITER = 5 // rotation stages
)(
  input wire clk,
  input wire rst_n,
  input wire [N_ACC-1:0] phase_acc,
  output reg [6:0] sine,
  output reg [6:0] cosine
);
  reg signed [6:0] x, xn, y, z, c_full, s_full;
  reg [1:0] quad;

  // Hardcoded (2 ** 7) * atan(2 ** -k) / (2 * pi) rounded to 7 bits
  function signed [6:0] atan_lut(input [3:0] k);
    case(k)
      4'd0: atan_lut = 7'd16;
      4'd1: atan_lut = 7'd9;
      4'd2: atan_lut = 7'd5;
      4'd3: atan_lut = 7'd3;
      4'd4: atan_lut = 7'd1;
      4'd5: atan_lut = 7'd1;
      default: atan_lut = 7'd0;
    endcase
  endfunction

  // CORDIC algorithm for first quadrant: approximate cos and sin with ever smaller rotations
  
  integer k;
  localparam signed [6:0] GAIN = 7'd19;

  always @(*) begin
      quad = phase_acc[N_ACC-1: N_ACC-2];
      z = phase_acc[N_ACC - 3: N_ACC - 7];
      y = '0;
      x = GAIN;  // Pre-compensates for CORDIC gain

      for (k = 0; k < N_ITER; k = k + 1) begin
        if (z >= 0) begin
          xn = x - (y >>> k);
          y = y + (x >>> k);
          x = xn;
          z = z - atan_lut(k);
        end
        else begin
          xn = x + (y >>> k);
          y = y - (x >>> k);
          x = xn;
          z = z + atan_lut(k);
        end
      end

    // Fold quadrants 2-4 to first by flipping and adjusting signs.

    case(quad)
      2'd0: begin c_full = x; s_full = y; end
      2'd1: begin c_full = -y; s_full = x; end
      2'd2: begin c_full = -x; s_full = -y; end
      2'd3: begin c_full = y; s_full = -x; end
    endcase
  end

  always @(posedge clk) begin // Register the result
    if (!rst_n) begin
      sine <= '0;
      cosine <= '0;
    end
    else begin
      sine <= s_full;
      cosine <= c_full;
    end
  end
  
endmodule