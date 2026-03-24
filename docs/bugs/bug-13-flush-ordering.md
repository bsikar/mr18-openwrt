# Bug 13: Flush Ordering

**Category**: Cache coherency
**Related docs**: [mips-memory-model](../technical/mips-memory-model.md), [address-map](../reference/address-map.md)

## Symptom

Bug 12 was fixed—the flush loop now correctly ran 4096 iterations, sweeping the full 128 KB. The `SDBBP` hit at the right time. The lzma-loader still reported "data error!" on boot.

## Root Cause

The D-cache flush was running **after** `load_image`, not before. The reasoning at the time was: "load the binary, then flush the cache to ensure coherency." This reasoning is exactly backwards.

Here is what happens when the flush runs after the load:

1. **`load_image`** writes the OpenWrt binary to physical RAM via KSEG1. Physical RAM is correct. D-cache still holds dirty Cisco lines.
2. **Flush trampoline** runs via KSEG0. The flush loop reads 128 KB of sequential KSEG0 addresses. These reads require cache lines.
3. The cache sets are full (they hold dirty Cisco lines). **LRU eviction fires**: the dirty Cisco lines are evicted and written back to physical RAM.
4. The Cisco write-back **overwrites the OpenWrt binary** that `load_image` just wrote.
5. Physical RAM now contains a mix of OpenWrt data and Cisco data, depending on which cache sets happened to evict during the flush.

The flush that was supposed to clean the cache is the operation that destroys the loaded binary.

## Fix

Move the flush to **Phase 0—before `load_image`**:

1. **Flush first**: Evict all dirty Cisco D-cache lines. The Cisco data is written back to physical RAM. This is harmless because we are about to overwrite that entire region with `load_image`.
2. **Load second**: `load_image` writes the OpenWrt binary to physical RAM via KSEG1. The D-cache is now clean (no dirty lines covering our binary's address range).
3. **Execute**: When the lzma-loader reads via KSEG0, every access is a cache miss. The cache fetches from physical RAM, which contains the correct OpenWrt data.

The critical insight is that the flush's write-back of stale data is harmless **only if it happens before the new data is loaded**. After the load, the same write-back is destructive.

See [mips-memory-model](../technical/mips-memory-model.md) for a full walkthrough of why "before" and "after" produce opposite results.

## Lesson

Cache flush timing relative to data writes is critical. The same flush operation produces opposite outcomes depending on whether it runs before or after the data load. "Before" writes back stale data to RAM that is about to be overwritten (harmless). "After" writes back stale data that overwrites the freshly loaded data (destructive).
