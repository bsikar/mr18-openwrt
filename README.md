# Meraki MR18 -> OpenWrt 25.12.0

Installing OpenWrt on a Meraki MR18 (QCA9558/AR9344, AR8035 PHY) via JTAG, with a persistent fix for the RGMII RX clock timing issue.

**Status: complete.** OpenWrt 25.12.0 runs from NAND, Ethernet is fully bidirectional.

---

## Directory Structure

```
mr18-openwrt/
+-- README.md
+-- docs/
|   +-- blog_mr18_openwrt.md    <- the full tutorial and project write-up
+-- firmware/
|   +-- openwrt-25.12.0-ath79-nand-meraki_mr18-initramfs-kernel.bin
|   +-- openwrt-25.12.0-ath79-nand-meraki_mr18-squashfs-sysupgrade.bin
+-- jtag/
|   +-- mr18.cfg                <- OpenOCD target config for MR18/QCA9558
|   +-- esp-prog.cfg            <- OpenOCD interface config for ESP-Prog
|   +-- mr18_flash.py           <- main flash automation script
|   +-- verify_asm.py           <- verifies hand-encoded MIPS machine code via disassembly
+-- ar8035-fix/
    +-- Makefile                <- cross-compilation build (make)
    +-- ar8035_start.S          <- MIPS O32 assembly _start (no libc)
    +-- ar8035.c                <- AR8035 MDIO fix source
    +-- ar8035-fix              <- compiled MIPS32 ELF (5592 bytes)
    +-- send_binary.py          <- transfers ar8035-fix to MR18 via UART hex
    +-- uart_transfer.py        <- transfers sysupgrade image via UART hex
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

1. Solder the JTAG header and wire up the ESP-Prog (see wiring diagram in the blog)
2. Download firmware (above)
3. Run `jtag/mr18_flash.py` -- this handles JTAG halt, RAM load, kernel boot, and failsafe trigger
4. Run `ar8035-fix/uart_transfer.py` -- transfers sysupgrade image over UART and flashes NAND
5. After reboot, deploy the AR8035 fix: `cd ar8035-fix && make` then use `send_binary.py`
6. Install the hotplug script on the MR18 for persistence across reboots

All scripts default to `/dev/ttyUSB4` for the ESP-Prog UART. If yours enumerates differently (`ls /dev/ttyUSB*`), edit the `UART` variable at the top of each script. The blog explains the wiring and device enumeration in detail.

---

## Building ar8035-fix

```sh
cd ar8035-fix/
make
```
