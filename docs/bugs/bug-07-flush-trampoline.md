# Bug 7: Flush Trampoline Timeout

**Category**: Cache coherency
**Related docs**: [mips-memory-model](../technical/mips-memory-model.md), [address-map](../reference/address-map.md)

## Symptom

The `FLUSH_TRAMPOLINE` -- a small MIPS program using `CACHE` instructions to writeback-invalidate D-cache and I-cache lines -- was loaded to the trampoline region and the CPU was resumed. The script waited for an `SDBBP` (debug breakpoint) at the end of the trampoline to signal completion. After 3 seconds, the wait timed out. The `SDBBP` was never hit.

## Root Cause

The `CACHE` instruction is a coprocessor 0 (CP0) operation that requires specific privilege and exception vector configuration. The trampoline was loaded to KSEG1 (`0xA0800000`, uncached) and the CPU was resumed at that address.

The likely failure mode: executing a `CACHE` instruction while running from KSEG1, with exception vectors pointing to the Nandloader's handlers (not ours), causes a cache error exception or other CP0 exception. The Nandloader's exception handler either hangs or jumps to an unexpected location. Either way, execution never reaches the `SDBBP` at the end of the trampoline.

The exact failure mode is difficult to diagnose because the exception vectors are in NAND-loaded Cisco code that we do not control, and JTAG access to the CPU is limited while the exception handler is running.

## Fix

Eliminated the need for `CACHE` instructions entirely by loading the binary to KSEG1 instead of KSEG0. Since KSEG1 is uncached, there are no D-cache lines to flush for the loaded data. The `D_CACHE_FLUSH_TRAMPOLINE` (which uses plain `lw` loads to evict stale lines via LRU replacement) replaced the `FLUSH_TRAMPOLINE` for the pre-load cache cleanup.

The `FLUSH_TRAMPOLINE` with `CACHE` instructions is still present in the code for belt-and-suspenders post-load use, but the critical path no longer depends on it.

## Lesson

When a workaround (flushing the cache with `CACHE` instructions) fails due to environmental constraints (wrong exception vectors, privilege issues), question whether the root problem can be eliminated rather than worked around differently. Loading to KSEG1 removes the D-cache involvement entirely, making the flush unnecessary for the loaded data.
