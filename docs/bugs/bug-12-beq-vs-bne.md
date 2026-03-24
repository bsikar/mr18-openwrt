# Bug 12: BEQ vs BNE Encoding

**Category**: Verification
**Related docs**: [mips-memory-model](../technical/mips-memory-model.md), [address-map](../reference/address-map.md)

## Symptom

The D-cache flush trampoline ran (SDBBP was hit after the expected time). But the lzma-loader still reported "data error!" on boot. The D-cache stale data problem ([Bug 11](bug-11-dcache-stale-data.md)) was not actually fixed.

## Root Cause

The flush loop's branch instruction was encoded with the wrong opcode. The MIPS branch instructions BEQ and BNE differ by a single bit in the opcode field:

| Instruction | Opcode (bits 31-26) | Binary | Behavior |
|-------------|---------------------|--------|----------|
| BEQ rs, rt, offset | `000100` | 4 | Branch if rs == rt |
| BNE rs, rt, offset | `000101` | 5 | Branch if rs != rt |

The flush loop was intended to iterate 4096 times (128 KB / 32 bytes per line), branching back to the loop top while `t0 != t1`. The hand-encoded instruction was:

```
0x11091FFD  (BEQ t0, t1, -3)
```

But the correct encoding for "branch while not equal" is:

```
0x1509FFFD  (BNE t0, t1, -3)
```

With `BEQ`, the loop only executed when `t0 == t1`, which is never true at the start. The loop body ran exactly once (the instruction executes before the branch is evaluated on the first iteration due to the pipeline), flushing a single cache line instead of 4096.

One cache line (32 bytes) out of 128 KB is 0.02% of the flush range -- effectively no flush at all.

## Fix

Change the branch instruction encoding:

```python
# Before (BEQ - wrong)
0x11091FFD

# After (BNE - correct)
0x1509FFFD
```

The `verify_asm.py` script was subsequently written to cross-check all hand-encoded MIPS instructions against Capstone disassembly, preventing this class of error from recurring.

## Lesson

A 1-bit difference in a MIPS opcode field changes program behavior completely. Hand-encoding machine code is error-prone, especially for instruction pairs that differ by a single bit (BEQ/BNE, BLT/BGE, SLL/SRL). Always verify hand-encoded instructions against an independent disassembler. The `verify_asm.py` tool exists specifically because of this bug.
