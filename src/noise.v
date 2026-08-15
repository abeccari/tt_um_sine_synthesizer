// White noise generator using maximal-length LFSR: https://en.wikipedia.org/wiki/Linear-feedback_shift_register

`default_nettype none

module noise #(
    parameter integer LFSR_BITS = 20,  // Number of bits in the LFSR
    parameter integer LFSR_TAP0 = 0,  // Feedback tap 0
    parameter integer LFSR_TAP1 = 17   // Feedback tap 1
)(
    input  wire clk,
    input  wire rst_n,
    input wire sample_en,
    output wire  out
);

    reg [LFSR_BITS-1:0] lfsr;
    
    always @(posedge clk) begin
        if (~rst_n)
            lfsr <= 20'h0abba;
        else if (sample_en)
            lfsr <= {(lfsr[LFSR_TAP1] ^ lfsr[LFSR_TAP0]) | (~|lfsr), lfsr[LFSR_BITS-1:1]};
            // The XOR with ^ (~|lfsr) prevents the LFSR from getting stuck at 0x0.
    end

    assign out = lfsr[0];

endmodule