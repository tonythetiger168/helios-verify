// tb_alu.v — self-checking testbench, prints "TEST PASS" or "TEST FAIL (n)"
`timescale 1ns/1ps

module tb_alu;
    reg  [7:0] a, b;
    reg  [2:0] op;
    wire [7:0] y;
    wire       zf;
    integer    errors = 0;

    alu dut (.a(a), .b(b), .op(op), .y(y), .zf(zf));

    task check(input [7:0] ea, input [7:0] eb, input [2:0] eop, input [7:0] ey);
    begin
        a = ea; b = eb; op = eop; #1;
        if (y !== ey) begin
            errors = errors + 1;
            $display("FAIL a=%h b=%h op=%0d y=%h exp=%h", ea, eb, eop, y, ey);
        end
    end
    endtask

    initial begin
        check(8'h05, 8'h03, 3'd0, 8'h08);  // add
        check(8'h05, 8'h03, 3'd1, 8'h02);  // sub
        check(8'hF0, 8'h0F, 3'd2, 8'h00);  // and
        check(8'hF0, 8'h0F, 3'd3, 8'hFF);  // or
        check(8'hF0, 8'h0F, 3'd4, 8'hFF);  // xor
        check(8'hAA, 8'h55, 3'd5, 8'h55);  // pass b
        check(8'h00, 8'h00, 3'd0, 8'h00);  // zero flag case

        if (errors == 0) $display("TEST PASS");
        else             $display("TEST FAIL (%0d)", errors);
        $finish;
    end
endmodule
