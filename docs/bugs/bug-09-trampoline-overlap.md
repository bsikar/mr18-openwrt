# Bug 9: Trampoline Overlaps Binary

**Category**: Verification
**Related docs**: [address-map](../reference/address-map.md)

## Symptom

During `cpu_scan_and_fix`, chunk 209 failed verification 3 consecutive times. Every other chunk passed on the first attempt. Chunk 209 had a 100% failure rate regardless of how many times it was rewritten.

## Root Cause

`TRAMPOLINE_ADDR` was set to `0xa0200000` (physical `0x00200000`, 2 MB into RAM). The XOR checksum program (14 words) was written to this address before each chunk verification.

Chunk 209 covered the address range `0xa01FFC00` to `0xa0201C00`. This range includes `0xa0200000` -- the trampoline address. The fix cycle was:

1. `cpu_scan_and_fix` detects chunk 209 has a bad XOR
2. Rewrites chunk 209 from the original file via `load_image` -- this correctly writes the binary data, **overwriting the XOR program** at `0xa0200000`
3. Writes the XOR program back to `0xa0200000` -- this **overwrites part of the binary** that was just loaded
4. Runs the XOR—the chunk now contains the XOR program instead of binary data, so the checksum is wrong again
5. Repeat forever

The trampoline and the binary data occupied the same physical memory, creating a write conflict that could never be resolved.

## Fix

Move `TRAMPOLINE_ADDR` to `0xa0800000` (physical `0x00800000`, 8 MB into RAM). The initramfs binary ends at approximately:

```
0xA005FC00 + 6,931,053 bytes = ~0xA06FBE6D
```

`0xA0800000` is ~1 MB above the binary end, well outside the loaded data range. No chunk's address range can overlap the trampoline.

## Lesson

Keep working memory (trampolines, scratch buffers, checksum result slots) above the data range being loaded and verified. When choosing addresses for helper programs that coexist with loaded data, compute the end address of the data and place helpers beyond it with a safe margin.
