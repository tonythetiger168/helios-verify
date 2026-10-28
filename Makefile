# Makefile — open-source EDA entry points for VerifierGate
TOP = tb_alu

.PHONY: lint sim synth formal clean

lint:   # Verilator lint (static check #1)
	verilator --lint-only -Wall --top-module $(TOP) -f files.f

sim:    # Icarus Verilog simulation (exit non-zero on TEST FAIL)
	iverilog -o sim.vvp -g2012 -f files.f
	vvp sim.vvp | tee sim.log
	grep -q "TEST PASS" sim.log

synth:  # Yosys synthesis check (static check #2)
	yosys -p "read_verilog -f files.f; hierarchy -top $(TOP); synth; check"

formal: # Yosys SAT on DUT (no assertions yet -> UNKNOWN is expected)
	yosys -p "read_verilog rtl/alu.v; prep -top alu; sat -verify -prove-asserts"

clean:
	rm -f sim.vvp sim.log
