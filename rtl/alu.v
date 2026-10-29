// alu.v — minimal demo DUT for the verifier platform
`timescale 1ns/1ps

module alu #(parameter W = 8) (
    input  wire [W-1:0] a,
    input  wire [W-1:0] b,
    input  wire [2:0]   op,
    output reg  [W-1:0] y,
    output wire         zf
);
    localparam OP_ADD  = 3'd0,
               OP_SUB  = 3'd1,
               OP_AND  = 3'd2,
               OP_OR   = 3'd3,
               OP_XOR  = 3'd4,
               OP_PASS = 3'd5;

    always @(*) begin
        case (op)
            OP_ADD:  y = a + b;
            OP_SUB:  y = a - b;
            OP_AND:  y = a & b;
            OP_OR:   y = a | b;
            OP_XOR:  y = a ^ b;
            OP_PASS: y = b;
            default: y = {W{1'b0}};
        endcase
    end

    assign zf = (y == {W{1'b0}});
endmodule
