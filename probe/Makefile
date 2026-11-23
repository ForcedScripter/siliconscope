# Build the C++ probes. Plain make, no CMake: two binaries, one header.
CXX      ?= g++
CXXFLAGS ?= -O2 -std=c++17 -Wall -Wextra -pthread
BUILD    := build

.PHONY: all clean
all: $(BUILD)/latency

$(BUILD)/%: %.cpp common.hpp | $(BUILD)
	$(CXX) $(CXXFLAGS) -o $@ $<

$(BUILD):
	mkdir -p $(BUILD)

clean:
	rm -rf $(BUILD)
