FROM verilator/verilator@sha256:a5b73e2fce0b2c483396f3800940f33b6faa802331a061c21694dd27b7352120
USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3-venv python3-pip iverilog yosys z3 ghdl unzip && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/eda-python && /opt/eda-python/bin/python -m pip install --no-cache-dir pyslang==12.1.0
RUN git clone --depth 1 --branch 2020.3.1 https://github.com/accellera-official/uvm-core.git /opt/uvm-core
ENV PATH="/opt/eda-python/bin:${PATH}" UVM_CORE=/opt/uvm-core/src UVM_JOBS=2
WORKDIR /work
ENTRYPOINT []
