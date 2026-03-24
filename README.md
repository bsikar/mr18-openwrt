# Meraki MR18 -> OpenWrt 25.12.0

Installing OpenWrt on a Meraki MR18 (QCA9558/AR9344, AR8035 PHY) via JTAG, with a persistent fix for the RGMII RX clock timing issue.

**Status: complete.** OpenWrt 25.12.0 runs from NAND, Ethernet is fully bidirectional.

---

## Directory Structure

```
mr18-openwrt/
+-- README.md
+-- docs/
|   +-- index.md                   <- documentation entry point
|   +-- overview.md                <- project context and scope
|   +-- guides/
|   |   +-- prerequisites.md       <- hardware and software requirements
|   |   +-- hardware-setup.md      <- JTAG wiring and soldering
|   |   +-- quickstart.md          <- step-by-step flash procedure
|   |   +-- troubleshooting.md     <- common failures and fixes
|   +-- technical/
|   |   +-- mips-memory-model.md   <- KSEG0/KSEG1 and cache coherency
|   |   +-- jtag-timing-attack.md  <- the 2-second boot window
|   |   +-- image-loading.md       <- load-verify-fix-launch pipeline
|   |   +-- mips-assembly.md       <- hand-encoded trampolines
|   |   +-- failsafe-trigger.md    <- getting into failsafe mode
|   |   +-- ar8035-phy-fix.md      <- fixing Ethernet RX
|   |   +-- uart-transfer.md       <- hex-over-serial protocol
|   |   +-- openocd-config.md      <- adapter and target setup
|   +-- reference/
|   |   +-- address-map.md         <- every address constant
|   |   +-- script-reference.md    <- CLI usage for all scripts
|   +-- bugs/
|       +-- index.md               <- all 23 bugs catalogued
|       +-- bug-01-wrong-binary.md .. bug-23-en-before-boot.md
+-- firmware/
|   +-- openwrt-25.12.0-ath79-nand-meraki_mr18-initramfs-kernel.bin
|   +-- openwrt-25.12.0-ath79-nand-meraki_mr18-squashfs-sysupgrade.bin
+-- jtag/
|   +-- mr18.cfg                   <- OpenOCD target config for MR18/QCA9558
|   +-- esp-prog.cfg               <- OpenOCD interface config for ESP-Prog
|   +-- mr18_flash.py              <- main flash automation script
|   +-- verify_asm.py              <- verifies hand-encoded MIPS machine code
+-- ar8035-fix/
    +-- Makefile                   <- cross-compilation build (make)
    +-- ar8035_start.S             <- MIPS O32 assembly _start (no libc)
    +-- ar8035.c                   <- AR8035 MDIO fix source
    +-- ar8035-fix                 <- compiled MIPS32 ELF (5592 bytes)
    +-- send_binary.py             <- transfers ar8035-fix to MR18 via UART hex
    +-- uart_transfer.py           <- transfers sysupgrade image via UART hex
```

---

## Firmware Downloads

Both images are from the [OpenWrt 25.12.0 release](https://downloads.openwrt.org/releases/25.12.0/targets/ath79/nand/):

```sh
wget https://downloads.openwrt.org/releases/25.12.0/targets/ath79/nand/openwrt-25.12.0-ath79-nand-meraki_mr18-initramfs-kernel.bin
wget https://downloads.openwrt.org/releases/25.12.0/targets/ath79/nand/openwrt-25.12.0-ath79-nand-meraki_mr18-squashfs-sysupgrade.bin

# Verify integrity:
echo "db191ecf0224f030365d604aa3919da9  openwrt-25.12.0-ath79-nand-meraki_mr18-initramfs-kernel.bin" | md5sum -c
echo "53e272bed2041616068c6958fe28a197  openwrt-25.12.0-ath79-nand-meraki_mr18-squashfs-sysupgrade.bin" | md5sum -c
# Both should print: OK
```

---

## Quick Start

See [docs/guides/quickstart.md](docs/guides/quickstart.md) for the full step-by-step procedure. The short version:

1. Solder the JTAG header and wire up the ESP-Prog
2. Run `jtag/mr18_flash.py` to halt the CPU, load the kernel, and trigger failsafe
3. Run `ar8035-fix/uart_transfer.py` to flash sysupgrade over UART
4. Deploy the AR8035 fix with `cd ar8035-fix && make` and `send_binary.py`

All scripts default to `/dev/ttyUSB4` for the ESP-Prog UART. If yours enumerates differently, edit the `UART` variable at the top of each script.

---

## Documentation

Full documentation lives in [`docs/index.md`](docs/index.md). Two reading paths are provided:

**Practical Path** (reproduce the flash):
Prerequisites -> Hardware Setup -> Quick Start -> Troubleshooting

**Technical Path** (understand the internals):
Overview -> MIPS Memory Model -> JTAG Timing Attack -> Image Loading -> MIPS Assembly -> Failsafe Trigger -> AR8035 PHY Fix -> UART Transfer -> OpenOCD Config

Reference material: [Address Map](docs/reference/address-map.md), [Script Reference](docs/reference/script-reference.md), [Bug Index](docs/bugs/index.md) (23 bugs catalogued).

---

## Building ar8035-fix

```sh
cd ar8035-fix/
make
```
