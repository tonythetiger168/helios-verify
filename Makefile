# Makefile — open-source EDA entry points for VerifierGate
TB_TOP = tb_alu
DUT_TOP = alu

.PHONY: lint sim synth formal clean

lint:   # Verilator lint (static check #1)
	verilator --lint-only -Wall --timing --top-module $(TB_TOP) -f files.f

sim:    # Icarus Verilog simulation (exit non-zero on TEST FAIL)
	iverilog -o sim.vvp -g2012 -f files.f
	vvp sim.vvp | tee sim.log
	grep -q "TEST PASS" sim.log

synth:  # Yosys synthesis check (static check #2)
	yosys -p "read_verilog rtl/alu.v; hierarchy -top $(DUT_TOP); synth; check"

formal: # Yosys SAT on DUT (no assertions; current empty proof succeeds)
	yosys -p "read_verilog rtl/alu.v; prep -top $(DUT_TOP); sat -verify -prove-asserts"

clean:
	rm -f sim.vvp sim.log
