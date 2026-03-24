# Project Overview

## What This Project Is

This project installs [OpenWrt](https://openwrt.org/) on a **Cisco Meraki MR18** 802.11n access point via JTAG, replacing the factory Cisco firmware with a fully open Linux distribution. It also provides a persistent fix for a hardware bug in the AR8035 Ethernet PHY that prevents RX traffic from reaching the SoC.

## Why It Exists

The Meraki MR18 is **cloud-locked by Cisco**. Without an active Meraki dashboard license:

- There is no local web UI—the device phones home to Cisco's cloud or does nothing.
- There is no TFTP recovery mode accessible without the Meraki bootloader password.
- There is no serial console shell—the Meraki Nandloader drops straight into a locked-down Linux environment.
- The bootloader disables JTAG probing within ~2 seconds of power-on.

**JTAG is the only way in.** This project exploits a narrow timing window during the Nandloader's early boot to halt the CPU via EJTAG, load an OpenWrt initramfs kernel directly into RAM, boot it, trigger failsafe mode, and flash a sysupgrade image to NAND. The entire process is automated with Python.

After OpenWrt is installed, a separate bare-metal MIPS binary (`ar8035-fix`) corrects the AR8035 PHY's RGMII RX clock delay bug, restoring bidirectional Ethernet. A hotplug script makes this fix persistent across reboots.

## Status

**Complete.** OpenWrt 25.12.0 (`ath79/nand`) runs from NAND. Ethernet is fully bidirectional.

## Hardware Summary

| Component   | Part                          | Details                                    |
|-------------|-------------------------------|--------------------------------------------|
| SoC         | QCA9557 / AR9344              | MIPS 74Kc, 560 MHz, big-endian             |
| Flash       | 128 MB NAND                   | Meraki Nandloader + OpenWrt sysupgrade     |
| RAM         | 128 MB DDR                    | Physical 0x00000000--0x07FFFFFF            |
| Wireless    | 2x 3x3 MIMO radios            | 2.4 GHz + 5 GHz (802.11n)                 |
| Ethernet    | AR8035 PHY                    | RGMII, single GbE port, PHY addr 3        |
| Power       | 802.3af PoE or 12 V barrel   | Project uses 12 V / 1.5 A bench supply    |
| JTAG adapter| ESP-Prog (FT2232H)            | Channel 0: JTAG, Channel 1 (UART): console + EN |

## Firmware

| Image | File | Size | MD5 |
|-------|------|------|-----|
| Initramfs | `openwrt-25.12.0-ath79-nand-meraki_mr18-initramfs-kernel.bin` | 6,931,053 bytes | `db191ecf0224f030365d604aa3919da9` |
| Sysupgrade | `openwrt-25.12.0-ath79-nand-meraki_mr18-squashfs-sysupgrade.bin` | - | `53e272bed2041616068c6958fe28a197` |

## Repository Layout

```
mr18-openwrt/
+-- README.md                   Quick-start summary and directory index
+-- docs/                       Full project documentation
|   +-- overview.md             This file—project overview
|   +-- guides/
|   |   +-- prerequisites.md    Hardware BOM, software deps, firmware downloads
|   +-- reference/
|   |   +-- address-map.md      Every address constant with derivation
|   |   +-- script-reference.md CLI usage for every script
|   +-- bugs/                   Bug investigation write-ups
|   +-- technical/              Deep-dive technical documents
+-- firmware/
|   +-- openwrt-...-initramfs-kernel.bin    RAM-bootable kernel (JTAG load target)
|   +-- openwrt-...-squashfs-sysupgrade.bin NAND flash image
+-- jtag/
|   +-- mr18_flash.py           Main automation: PSU, OpenOCD, JTAG halt, RAM load, boot
|   +-- verify_asm.py           Validates hand-encoded MIPS machine code via Capstone
|   +-- mr18.cfg                OpenOCD target config for AR9344 / QCA9557
|   +-- esp-prog.cfg            OpenOCD interface config for ESP-Prog (FT2232H)
+-- ar8035-fix/
    +-- Makefile                Cross-compilation build (make / make docker / make clean)
    +-- ar8035_start.S          MIPS O32 assembly _start (no libc, raw syscalls)
    +-- ar8035.c                AR8035 MDIO register fix (RX clock delay + hibernation)
    +-- ar8035-fix              Pre-built MIPS32 ELF binary (5592 bytes)
    +-- send_binary.py          Transfers ar8035-fix to MR18 over UART and runs it
    +-- uart_transfer.py        Transfers sysupgrade image over UART, auto-runs sysupgrade
```

## Documentation Index

See [docs/guides/prerequisites.md](guides/prerequisites.md) for hardware and software requirements. See [docs/reference/address-map.md](reference/address-map.md) for the memory map and address derivations. See [docs/reference/script-reference.md](reference/script-reference.md) for script CLI usage and configuration.
