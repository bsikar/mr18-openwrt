# Bug 1: Wrong Binary (ar71xx vs ath79)

**Category**: Toolchain
**Related docs**: [address-map](../reference/address-map.md), [mips-memory-model](../technical/mips-memory-model.md)

## Symptom

The ar71xx initramfs image was loaded via JTAG. The CPU resumed and the lzma-loader began executing. Nothing happened -- no Ethernet link, no ping response, no serial output. The LZMA decompressor appeared to die silently.

## Root Cause

The ar71xx and ath79 targets produce different lzma-loader binaries with different startup sequences. The critical difference is in BSS initialization relative to self-relocation:

- **ar71xx lzma-loader**: Zeroes BSS **before** relocating itself. The BSS section overlaps the LZMA compressed data region, so zeroing BSS wipes the compressed payload before decompression can begin.
- **ath79 lzma-loader**: Relocates itself to `0x82800000` (KSEG0) **first**, then zeroes BSS at the new (relocated) address. The original LZMA data at the load address is untouched.

The ar71xx loader's BSS-first ordering is correct for a normal NAND boot where the bootloader has already separated code and data, but fatal when the entire binary is loaded as a flat image via JTAG.

## Fix

Switch to the ath79 binary:

```
openwrt-25.12.0-ath79-nand-meraki_mr18-initramfs-kernel.bin
```

The ath79 target's lzma-loader preserves the LZMA payload through relocation, allowing decompression to succeed after a JTAG flat load.

## Lesson

When two OpenWrt targets exist for the same hardware (ar71xx and ath79 both support the QCA9557/AR9344), the device compatibility table is not sufficient to choose between them. Check the loader startup sequence -- specifically, the ordering of BSS initialization relative to self-relocation -- to determine which target is safe for JTAG flat loading.
