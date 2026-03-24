# Bug 11: D-Cache Stale Data

**Category**: Cache coherency
**Related docs**: [mips-memory-model](../technical/mips-memory-model.md), [address-map](../reference/address-map.md)

## Symptom

All 847 chunks verified clean via `cpu_scan_and_fix`. The full-binary XOR matched the expected value. Every verification layer passed. The lzma-loader still reported "data error!" on boot.

## Root Cause

This is the central cache coherency bug of the project. The failure comes from the MIPS dual-segment memory architecture:

**Step 1—Nandloader fills D-cache**: When the MR18 powers on, the Nandloader reads the Cisco kernel from NAND and writes it to `0x8005FC00` (KSEG0, cached). These writes allocate D-cache lines and mark them dirty. The Cisco kernel data sits in the D-cache.

**Step 2—JTAG overwrites physical RAM**: Our `load_image` writes the OpenWrt binary to `0xA005FC00` (KSEG1, uncached). These writes go directly to physical RAM. The D-cache is not informed—it still holds dirty Cisco data for the same physical addresses.

**Step 3—Verification sees correct data**: Our XOR checksum programs read via KSEG1 (uncached), which reads physical RAM directly. Physical RAM contains our correct OpenWrt binary. All checksums pass.

**Step 4—Execution sees stale data**: The lzma-loader runs via KSEG0 (cached). When it reads the compressed payload, the D-cache serves the stale Cisco data from its dirty lines instead of the correct OpenWrt data in physical RAM. The LZMA stream is corrupted, and decompression fails.

The stale D-cache lines persist because nothing has evicted them. They were loaded by the Nandloader (before our JTAG halt), and our KSEG1 writes never touch the cache.

## Fix

Add a 128 KB KSEG0 read loop **before** `load_image` to evict all stale D-cache lines via LRU replacement:

```
lui  t0, 0x8000        # t0 = 0x80000000
lui  t1, 0x8002        # t1 = 0x80020000 (128 KB)
lw   t2, 0(t0)         # LOOP: load from KSEG0 (triggers LRU eviction)
addiu t0, t0, 32       # advance by cache line size
bne  t0, t1, LOOP      # repeat for all 128 KB
nop                     # branch delay slot
sdbbp                   # signal completion
```

The 128 KB sweep covers 4x the 32 KB D-cache capacity (4-way set-associative), guaranteeing that every way of every set is evicted. The dirty Cisco lines are written back to physical RAM (harmless—we are about to overwrite that RAM with `load_image`), and the cache is left in a clean state.

After this flush, `load_image` writes the OpenWrt binary to physical RAM via KSEG1. When the lzma-loader later reads via KSEG0, the D-cache has no stale lines—every access is a cache miss that fetches the correct data from physical RAM.

See [mips-memory-model](../technical/mips-memory-model.md) for a detailed explanation of the D-cache dirty line problem and why the flush must run before the load, not after.

## Lesson

Verification through one memory path (KSEG1, uncached) does not guarantee correctness through another memory path (KSEG0, cached). When data is written via an uncached path and later consumed via a cached path, stale D-cache lines from a previous boot stage can silently override the correct data in physical RAM.
