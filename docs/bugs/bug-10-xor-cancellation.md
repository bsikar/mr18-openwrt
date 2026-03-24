# Bug 10: XOR Cancellation

**Category**: Verification
**Related docs**: [mips-memory-model](../technical/mips-memory-model.md)

## Symptom

The full-binary XOR checksum matched the expected value `0xf524142e`. All verification passed. The lzma-loader still reported "data error!" on boot.

## Root Cause

XOR checksums have a fundamental algebraic weakness: if two words are corrupted and their XOR deltas are identical, the errors cancel:

```
Expected:  A ^ B ^ C ^ ... = X
Actual:    A' ^ B' ^ C ^ ... = X
           where A' = A ^ delta, B' = B ^ delta
           so A' ^ B' = (A ^ delta) ^ (B ^ delta) = A ^ B
```

The corrupted checksum equals the expected checksum because the two error deltas XOR to zero.

With approximately 60 corrupted words per load (at the ~0.04% PRACC error rate across 1.7 million words), the probability of at least one cancellation pair is significant. In combinatorial terms, with 60 errors and 32-bit deltas, the birthday-style collision probability is non-trivial—and only a single cancellation is needed to mask two errors.

This bug demonstrates that a passing full-binary XOR is necessary but not sufficient proof of correctness.

## Fix

Never trust a full-binary XOR result alone. The verification pipeline now requires both:

1. **Chunk-level scan**: `cpu_scan_and_fix` verifies each 8 KB chunk individually. A cancellation within a single 8 KB chunk requires two errors in the same 2048-word region—far less probable than two errors anywhere in the 1.7-million-word binary.
2. **Full-binary XOR**: Run after all chunks pass. Both must agree before launching the kernel.

If the full XOR passes but any chunk fails, the chunk result takes precedence (the full XOR has a cancellation). If all chunks pass but the full XOR fails, something else is wrong (retry from scratch).

## Lesson

XOR checksums cannot detect even numbers of errors whose deltas cancel. This is an inherent property of XOR's algebraic structure. For high-reliability verification, use layered checksums at multiple granularities. A full-file checksum catches most errors; per-chunk checksums catch the cancellations that slip through.
