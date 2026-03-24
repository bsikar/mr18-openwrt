# Bug 8: Phantom Verify Errors

**Category**: Verification
**Related docs**: [mips-memory-model](../technical/mips-memory-model.md), [address-map](../reference/address-map.md)

## Symptom

The `verify_and_fix` routine reported "fixing" 15 words -- it detected mismatches between RAM contents and the original file, then rewrote those words. But the full-binary XOR checksum was unchanged: `0x3137a4af` both before and after the "fixes." The lzma-loader still produced "data error!" on boot.

## Root Cause

The `verify_and_fix` routine used PRACC bulk reads (`mdw` via OpenOCD) to read back each word from RAM and compare it against the file. But PRACC reads have the same bit-flip rate as PRACC writes (see [Bug 4](bug-04-pracc-bit-errors.md)).

This creates two classes of errors that cancel out:

1. **Phantom errors (false positives)**: A word in RAM is correct, but the PRACC read returns a bit-flipped value. The routine "detects" a mismatch and rewrites the word with the correct value -- which was already there. No harm, but no help either.

2. **Missed errors (false negatives)**: A word in RAM is genuinely corrupt, but the PRACC read returns the corrupt value with an additional bit flip that happens to produce the expected value. The routine sees a match and moves on, leaving the corruption in place.

With ~0.04% bit-flip rate on reads, verifying 6.9 MB of data (~1.7 million words) means roughly 700 phantom reads. The 15 "fixes" were almost certainly all phantom errors, while the real corrupt words were hidden by read errors masking them as correct.

The unchanged XOR confirms this: if real corruptions were being fixed, the checksum would change.

## Fix

Replace PRACC bulk reads with CPU-executed checksums. The `cpu_scan_and_fix` approach:

1. Write a 14-word XOR program to the trampoline region
2. Set CPU registers to define the chunk start/end addresses
3. Resume the CPU (it executes the XOR at native speed, error-free)
4. Read the single 32-bit result via PRACC (1 word, not thousands)
5. Compare against the expected chunk XOR

This reduces PRACC exposure from ~2000 reads per 8 KB chunk to just 5 PRACC operations per chunk (write program once, then per-chunk: 2 register writes + resume + 1 result read + compare). At 5 ops per chunk, the probability of a phantom error affecting the result is negligible.

## Lesson

If your verification mechanism uses the same error-prone channel as your data transfer, the verification inherits the same error rate. The verification must use a fundamentally different path -- in this case, CPU-internal ALU operations -- to provide a reliable ground truth.
