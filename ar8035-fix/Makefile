# AR8035 RGMII RX clock delay fix for Meraki MR18
# Cross-compiles for MIPS32 big-endian (AR9344 / MIPS 74Kc, no hardware FPU)
#
# Prerequisites:
#   Debian/Ubuntu: apt install gcc-mips-linux-gnu binutils-mips-linux-gnu
#   Arch Linux:    docker run --rm -v $(pwd):/work -w /work debian:bookworm-slim \
#                    sh -c "apt-get update -q && apt-get install -y -q gcc-mips-linux-gnu make file && make"
#
# UART device:
#   The send_binary.py and uart_transfer.py scripts default to /dev/ttyUSB4
#   (ESP-Prog FT2232H interface B). Edit the UART variable in each script
#   if your device enumerates differently.

CROSS   = mips-linux-gnu-
CC      = $(CROSS)gcc
STRIP   = $(CROSS)strip

CFLAGS  = -O2 -msoft-float -mno-abicalls -fno-pic
LDFLAGS = -nostdlib -nostartfiles -Wl,-z,noexecstack -Wl,-e,_start -static

TARGET  = ar8035-fix
SRCS    = ar8035_start.S ar8035.c

.PHONY: all clean docker

all: $(TARGET)

docker:
	docker run --rm -v $(CURDIR):/work -w /work debian:bookworm-slim \
		sh -c "apt-get update -q && apt-get install -y -q gcc-mips-linux-gnu make file && make"

$(TARGET): $(SRCS)
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $^
	$(STRIP) $@
	@ls -l $@
	@echo "Built: $@ ($$(wc -c < $@) bytes, $$(file $@))"

clean:
	rm -f $(TARGET)
