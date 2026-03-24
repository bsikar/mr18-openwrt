# Bug 4: PRACC Write Bit Errors

**Category**: JTAG/PRACC
**Related docs**: [mips-memory-model](../technical/mips-memory-model.md), [address-map](../reference/address-map.md)

## Symptom

After a successful `load_image` (correct duration, proper completion message), the lzma-loader reported "data error!" during decompression. Comparing a 45 KB sample of RAM contents against the original file revealed 18 single-bit flips scattered across the region.

## Root Cause

The bit errors are not caused by signal integrity (noise, reflections, crosstalk on the JTAG wires). They are PRACC handshake protocol errors -- timing violations in the MIPS EJTAG Processor Access (PRACC) state machine that mediates memory writes through the debug interface.

Key evidence: the error rate does not change when JTAG clock speed is reduced. At both 1000 kHz and 100 kHz adapter speeds, the per-word bit-flip probability remains approximately the same. Signal integrity issues would improve at lower clock rates; PRACC protocol errors do not, because they are a function of the handshake state machine, not the wire timing.

The error rate is approximately 1 corrupted word per 2500 words written (~0.04%), which means roughly 60 corrupted words across a 6.9 MB binary load.

## Fix

Since PRACC bit errors cannot be prevented at the transport level, the solution is a verify-and-correct layer:

1. **Detect**: After `load_image`, run a CPU-executed XOR checksum program (not a PRACC bulk read, which would inherit the same error rate -- see [Bug 8](bug-08-phantom-verify-errors.md)).
2. **Locate**: `cpu_scan_and_fix` divides the binary into 847 chunks of 8 KB each. For each chunk, a CPU-executed XOR program computes the checksum using only 5 PRACC operations (write program, set registers, resume, read result, compare).
3. **Fix**: Any chunk with a mismatched XOR is rewritten from the file via `load_image` and re-verified.

This approach uses the CPU's own ALU (error-free) for computation, minimizing PRACC exposure to just a few register reads/writes per chunk.

## Lesson

When you cannot prevent write errors on a transport channel, build a robust verification layer. The key insight is that the verification mechanism must not use the same error-prone path as the data transfer. CPU-executed checksums (5 PRACC ops) are far more reliable than PRACC bulk reads (thousands of PRACC ops).
