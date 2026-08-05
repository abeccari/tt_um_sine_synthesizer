/*
 * Copyright (c) 2026 Alberto Beccari
 * SPDX-License-Identifier: Apache-2.0
 *
 * tt_um_abeccari_swsynth -- CORDIC sine-wave synthesizer.
 *
 *   ui_in[7:0]   FREQ      : 8-bit frequency word (exponential map, 10 Hz - 20 kHz)
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
  localparam integer N_ACC = 24;  // phase-accumulator width (freq resolution)
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
  // 1. Input synchroniser : ui_in is asynchronous to clk -> 2-FF sync -> freq_word
  //    TODO
  // ---------------------------------------------------------------------------

  // ---------------------------------------------------------------------------
  // 2. Frequency map : 8-bit freq_word -> N_ACC-bit phase_inc
  //    (exponential law: 256-entry ROM for bit-exact, or float-decode for area)
  //    TODO
  // ---------------------------------------------------------------------------

  // ---------------------------------------------------------------------------
  // 3. Sample-rate strobe : drive sample_en high for one cycle at clk/256 (48 kHz)
  //    TODO
  // ---------------------------------------------------------------------------

  // ---------------------------------------------------------------------------
  // 4. Phase accumulator (NCO) : phase_acc += phase_inc on sample_en, wraps mod 2^N
  //    TODO
  // ---------------------------------------------------------------------------

  // ---------------------------------------------------------------------------
  // 5. CORDIC core (rotation mode) + quadrant fold : phase code -> signed sin/cos
  //    TODO
  // ---------------------------------------------------------------------------

  wire signed [SW-1:0] sample_s = '0;
  wire [SW-1:0] sample_u = {~sample_s[SW-1], sample_s[SW-2:0]};

  // ---------------------------------------------------------------------------
  // 6a. Output format : round + saturate signed SW -> OW, then to offset-binary.
  //     Drive sine_ob and cos_ob (64 = zero-crossing).
  //     TODO
  // ---------------------------------------------------------------------------

  // ---------------------------------------------------------------------------
  // 6b. Sigma-delta : 1st-order modulator at full clk rate (OSR = 256), fed the
  //     FULL-precision sample (not the 7-bit value). Drive pdm_bit.
  // ---------------------------------------------------------------------------

  sigma_delta #(.W(SW)) u_dsm (
      .clk(clk), .rst_n(rst_n), .x(sample_u), .pdm_bit(pdm_bit)
  );

  // ---------------------------------------------------------------------------
  // 7. Pin mapping
  // ---------------------------------------------------------------------------
  assign uo_out  = {pdm_bit, sine_ob};    // [7]=PDM, [6:0]=sine (offset-binary)
  assign uio_out = {cos_ob, sample_en};   // [7:1]=cosine, [0]=SAMPLE_EN
  assign uio_oe  = 8'hFF;                  // all bidir pins driven as outputs

  // List all unused inputs to prevent warnings.
  wire _unused = &{ena, uio_in, 1'b0};

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
