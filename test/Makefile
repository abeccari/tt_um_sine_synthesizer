# Makefile
# See https://docs.cocotb.org/en/stable/quickstart.html for more info

# defaults
SIM ?= icarus
FST ?= -fst # Use more efficient FST format
TOPLEVEL_LANG ?= verilog
SRC_DIR = $(PWD)/../src
PROJECT_SOURCES = project.v

ifneq ($(GATES),yes)

# RTL simulation:
SIM_BUILD				= sim_build/rtl
VERILOG_SOURCES += $(addprefix $(SRC_DIR)/,$(PROJECT_SOURCES))

else

# Gate level simulation:
SIM_BUILD				= sim_build/gl
COMPILE_ARGS    += -DGL_TEST
COMPILE_ARGS    += -DFUNCTIONAL
COMPILE_ARGS    += -DSIM
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13g2/libs.ref/sg13g2_io/verilog/sg13g2_io.v
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13g2/libs.ref/sg13g2_stdcell/verilog/sg13g2_stdcell.v

# this gets copied in by the GDS action workflow
VERILOG_SOURCES += $(PWD)/gate_level_netlist.v

endif

# Allow sharing configuration between design and testbench via `include`:
COMPILE_ARGS 		+= -I$(SRC_DIR)

# project.v uses SystemVerilog constructs ('0, signed functions), so Icarus needs -g2012.
COMPILE_ARGS 		+= -g2012

# Include the testbench sources:
VERILOG_SOURCES += $(PWD)/tb.v
TOPLEVEL = tb

# List test modules to run, separated by commas and without the .py suffix:
COCOTB_TEST_MODULES = test

# Customize cocotb's log prefix
export COCOTB_LOG_PREFIX = {record.levelname:<16}

# Optional waveform PNGs: run `make PLOT_WAVES=1` (needs matplotlib: uv sync --extra plot).
# `export` makes the command-line value visible to the cocotb (Python) process.
export PLOT_WAVES

# include cocotb's make rules to take care of the simulator setup
include $(shell cocotb-config --makefiles)/Makefile.sim

# Render a tone to a .wav so you can listen to it (separate, on-demand -- not part
# of the normal test run). Config via env vars, e.g.:
#   uv run make wav
#   WAV_NOTE=7 WAV_OCT=8 WAV_SECONDS=5 uv run make wav
.PHONY: wav
wav:
	$(MAKE) COCOTB_TEST_MODULES=wav_export
