# Hand-Encoded MIPS Machine Code

Every MIPS instruction used in the project's trampolines, encoded by hand in Python as 32-bit big-endian integer constants. This document shows the bit-level derivation of each instruction word so that someone can modify or extend the assembly without an assembler toolchain.

## MIPS32 Instruction Formats

All MIPS32 instructions are exactly 32 bits (4 bytes). There are three formats:

### R-Type (Register)

Used for register-to-register operations (XOR, etc.).

```
 31    26 25   21 20   16 15   11 10    6 5     0
+--------+------+------+------+------+--------+
|   op   |  rs  |  rt  |  rd  |  sa  | funct  |
| 6 bits | 5 b  | 5 b  | 5 b  | 5 b  | 6 bits |
+--------+------+------+------+------+--------+
```

- **op**: Always `0x00` for SPECIAL (R-type ALU operations)
- **rs**: First source register
- **rt**: Second source register
- **rd**: Destination register
- **sa**: Shift amount (0 for non-shift operations)
- **funct**: Function code (selects the specific operation)

Encoding formula:

```python
def R(op, rs, rt, rd, sa, funct):
    return (op << 26) | (rs << 21) | (rt << 16) | (rd << 11) | (sa << 6) | funct
```

### I-Type (Immediate)

Used for operations with a 16-bit immediate value (LUI, ORI, ADDIU, LW, SW, BNE, CACHE).

```
 31    26 25   21 20   16 15                   0
+--------+------+------+------------------------+
|   op   |  rs  |  rt  |       immediate        |
| 6 bits | 5 b  | 5 b  |       16 bits          |
+--------+------+------+------------------------+
```

- **op**: Opcode (identifies the instruction)
- **rs**: Source register (or base register for loads/stores)
- **rt**: Destination register (or second source for branches)
- **immediate**: 16-bit value (sign-extended or zero-extended depending on instruction)

Encoding formula:

```python
def I(op, rs, rt, imm):
    return (op << 26) | (rs << 21) | (rt << 16) | (imm & 0xFFFF)
```

### J-Type (Jump)

Used for unconditional jumps to absolute addresses.

```
 31    26 25                                   0
+--------+--------------------------------------+
|   op   |            target                    |
| 6 bits |            26 bits                   |
+--------+--------------------------------------+
```

- **op**: `0x02` for J (jump)
- **target**: 26-bit word-aligned address (see encoding details below)

Encoding formula:

```python
def J(op, target26):
    return (op << 26) | (target26 & 0x3FFFFFF)
```

## Register Conventions

The trampolines use only temporary registers to avoid clobbering any state the Nandloader or lzma-loader might depend on:

| Register | Number | ABI Name | Used For |
|----------|--------|----------|----------|
| `$zero`  | 0      | `$zero`  | Hardwired zero |
| `$t0`    | 8      | `$t0`    | Loop pointer / base address |
| `$t1`    | 9      | `$t1`    | Loop end address |
| `$t2`    | 10     | `$t2`    | XOR accumulator / scratch |
| `$t3`    | 11     | `$t3`    | Loaded word (scratch) |
| `$t4`    | 12     | `$t4`    | Result store base address |

The `$t` registers (temporaries, `$t0`-`$t9` = registers 8-15, 24-25) are caller-saved in the MIPS O32 ABI. Any called function is free to clobber them. Since our trampolines are standalone programs that do not return to the caller, using `$t` registers is safe and avoids any risk of corrupting the Nandloader's saved register state.

## Key Opcodes

| Mnemonic | Op/Funct | Format | Encoding | Description |
|----------|----------|--------|----------|-------------|
| LUI      | op=`0x0F` | I-type | `[001111][00000][rt][imm16]` | Load Upper Immediate: `rt = imm16 << 16` |
| ORI      | op=`0x0D` | I-type | `[001101][rs][rt][imm16]`    | OR Immediate: `rt = rs \| zero_extend(imm16)` |
| ADDIU    | op=`0x09` | I-type | `[001001][rs][rt][imm16]`    | Add Immediate Unsigned: `rt = rs + sign_extend(imm16)` |
| LW       | op=`0x23` | I-type | `[100011][base][rt][offset]` | Load Word: `rt = mem[base + sign_extend(offset)]` |
| SW       | op=`0x2B` | I-type | `[101011][base][rt][offset]` | Store Word: `mem[base + sign_extend(offset)] = rt` |
| BNE      | op=`0x05` | I-type | `[000101][rs][rt][offset]`   | Branch if Not Equal: if `rs != rt`, branch |
| XOR      | op=`0x00`, funct=`0x26` | R-type | `[000000][rs][rt][rd][00000][100110]` | Exclusive OR: `rd = rs ^ rt` |
| J        | op=`0x02` | J-type | `[000010][target26]`         | Jump to address |
| CACHE    | op=`0x2F` | I-type | `[101111][base][op5][offset]` | Cache maintenance operation |
| SDBBP    | op=`0x1C`, funct=`0x3F` | R-type | `[011100][0...0][111111]` | Software Debug Breakpoint |
| NOP      | -- | R-type | `0x00000000` | No operation (SLL $zero, $zero, 0) |

## Building 32-Bit Constants: LUI + ORI

MIPS has no single instruction that loads a full 32-bit constant into a register. The standard pattern is:

```
LUI  rt, upper16       # rt = upper16 << 16  (lower 16 bits are zero)
ORI  rt, rt, lower16   # rt = rt | lower16   (fills in the lower 16 bits)
```

Example: load `0xA005FC00` into `$t0` (register 8):

```
LUI  $t0, 0xA005       # $t0 = 0xA0050000
ORI  $t0, $t0, 0xFC00  # $t0 = 0xA005FC00
```

### Why ORI, Not ADDIU, for the Lower Half

ADDIU sign-extends its 16-bit immediate before adding. If the lower 16 bits have bit 15 set (i.e., the value is `0x8000` or higher), sign extension produces a negative number:

```
ADDIU $t0, $t0, 0x8000
# sign_extend(0x8000) = 0xFFFF8000 = -32768
# $t0 = 0xA0050000 + 0xFFFF8000 = 0xA0048000   <-- WRONG
```

ORI zero-extends its immediate, so `0x8000` stays `0x00008000`:

```
ORI $t0, $t0, 0x8000
# zero_extend(0x8000) = 0x00008000
# $t0 = 0xA0050000 | 0x00008000 = 0xA0058000   <-- CORRECT
```

This trap is specifically relevant in the D-cache flush trampoline where the `FLUSH_TRAMPOLINE` sets the end address with `ORI t1, t1, 0x8000`:

```python
0x35298000,  # ori t1, t1, 0x8000  <- 32KB; ori zero-extends, addiu would sign-extend
```

The `D_CACHE_FLUSH_TRAMPOLINE` avoids this by using `LUI` alone (the end address `0x80020000` has `0x0000` in its lower half).

ADDIU is safe when the lower 16 bits are in the range `0x0000`-`0x7FFF` (bit 15 = 0). The trampolines use ADDIU for small increments like `+4` and `+32` where sign extension is not a concern.

## Branch Delay Slots

Every MIPS branch and jump instruction has a **branch delay slot**: the instruction immediately following the branch is always executed, regardless of whether the branch is taken. This is a fundamental MIPS pipeline artifact.

```
    BNE  $t0, $t1, target    # branch instruction
    NOP                       # delay slot -- ALWAYS executes
    # if branch taken: execution continues at target
    # if branch not taken: execution continues here
```

All trampolines in this project fill the delay slot with NOP (`0x00000000`). Using a useful instruction in the delay slot is a common optimization, but for hand-encoded assembly clarity and correctness are more important than saving one cycle.

## Branch Offset Encoding

BNE (and BEQ) use a 16-bit signed offset relative to the instruction after the branch (PC+4), measured in **words** (4-byte units):

```
offset_words = target_instruction_index - (branch_instruction_index + 1)
```

The `+1` accounts for the fact that the CPU has already advanced PC to the next instruction (the delay slot) when it evaluates the branch target.

Example from the XOR checksum program:

```
Instruction 5: LW   (loop target)
Instruction 6: XOR
Instruction 7: ADDIU
Instruction 8: BNE  $t0, $t1, ???   <-- branch back to instruction 5
Instruction 9: NOP  (delay slot)
```

```
offset = 5 - (8 + 1) = 5 - 9 = -4
```

The immediate field stores `-4` as a 16-bit two's complement value:

```
-4 in 16-bit two's complement = 0xFFFC
```

So the BNE instruction word is:

```
I(0x05, T0, T1, 0xFFFC) = 0x1509FFFC
```

## J-Type Target Encoding

The J instruction's 26-bit target field encodes a word-aligned address with the top 4 bits supplied by PC+4:

```
target26 = (address >> 2) & 0x3FFFFFF
```

The `>> 2` removes the two lowest bits (which are always zero for word-aligned addresses). The top 4 bits of the effective jump address come from `PC+4[31:28]`.

Example: `J 0xA0060000` from `PC = 0xA0800000`:

```
target26 = (0xA0060000 >> 2) & 0x3FFFFFF
         = 0x28018000 & 0x3FFFFFF
         = 0x0018000

Effective address = (PC+4)[31:28] | (target26 << 2)
                  = (0xA0800004)[31:28] | (0x0018000 << 2)
                  = 0xA0000000 | 0x00060000
                  = 0xA0060000  [correct]
```

The J instruction word is:

```
J(0x02, 0x0018000) = (0x02 << 26) | 0x0018000 = 0x08018000
```

This works because both the trampoline (`0xA0800000`) and the target (`0xA0060000`) share the same top nibble (`0xA`). If they were in different 256 MB regions, a J instruction could not reach the target, and JR (jump register) with a full 32-bit address would be needed.

## CACHE Instruction Encoding

The CACHE instruction uses I-type format with a special interpretation of the `rt` field:

```
 31    26 25   21 20   16 15                   0
+--------+------+------+------------------------+
| 101111 | base | op5  |       offset           |
| 0x2F   | 5 b  | 5 b  |       16 bits          |
+--------+------+------+------------------------+
```

- **base**: Register holding the base address
- **op5**: 5-bit cache operation code (not a register)
- **offset**: Signed 16-bit byte offset from base

Cache operation codes used:

| op5 | Value | Operation |
|-----|-------|-----------|
| `0x00` | 0 | I-cache Index Invalidate |
| `0x01` | 1 | D-cache Index Writeback Invalidate |

Example: `CACHE 0x01, 0($t0)` -- D-cache writeback invalidate at address in `$t0`:

```
(0x2F << 26) | (T0 << 21) | (0x01 << 16) | 0
= (0x2F << 26) | (8 << 21)  | (1 << 16)    | 0
= 0xBC000000   | 0x01000000 | 0x00010000   | 0
= 0xBD010000
```

## SDBBP (Software Debug Breakpoint)

SDBBP causes the CPU to enter debug exception mode, which OpenOCD detects as a halt. It is encoded as a SPECIAL2 instruction:

```
 31    26 25                          6 5     0
+--------+----------------------------+--------+
| 011100 |       code (20 bits)       | 111111 |
| 0x1C   |       0x00000             | 0x3F   |
+--------+----------------------------+--------+
```

```
SDBBP = (0x1C << 26) | 0x3F = 0x70000000 | 0x3F = 0x7000003F
```

The 20-bit `code` field is zero (unused). OpenOCD's `wait_halt` command detects the debug exception and reports the CPU as halted. The PC will point to the SDBBP instruction itself.

---

## Trampoline 1: D-Cache Flush (8 Words)

**Purpose**: Evict all dirty D-cache lines to physical RAM before `load_image` writes our binary. Uses plain LW reads through KSEG0 to trigger LRU eviction (no privileged CACHE instructions).

**Location in code**: `D_CACHE_FLUSH_TRAMPOLINE` in `mr18_flash.py`

**Runs at**: `TRAMPOLINE_ADDR` (`0xA0800000`, KSEG1 uncached)

### Word-by-Word Encoding

**Word 0: `0x3C088000` -- `LUI $t0, 0x8000`**

Set loop start to KSEG0 base address.

```
I-type: op=0x0F, rs=0, rt=8($t0), imm=0x8000
(0x0F << 26) | (0 << 21) | (8 << 16) | 0x8000
= 0x3C000000  | 0x00000000 | 0x00080000 | 0x8000
= 0x3C088000

Result: $t0 = 0x80000000
```

**Word 1: `0x3C098002` -- `LUI $t1, 0x8002`**

Set loop end to KSEG0 base + 128 KB. The value `0x80020000` has `0x0000` in its lower half, so LUI alone is sufficient (no ORI needed).

```
I-type: op=0x0F, rs=0, rt=9($t1), imm=0x8002
(0x0F << 26) | (0 << 21) | (9 << 16) | 0x8002
= 0x3C098002

Result: $t1 = 0x80020000
```

128 KB = 4 x 32 KB D-cache = sweeps all 4 ways of every cache set.

**Word 2: `0x8D0A0000` -- `LW $t2, 0($t0)` [LOOP HEAD]**

Read one word from KSEG0. The read value is discarded; the purpose is to occupy a cache line, forcing LRU eviction of whatever was there before (potentially a dirty Cisco line).

```
I-type: op=0x23, rs=8($t0), rt=10($t2), imm=0
(0x23 << 26) | (8 << 21) | (10 << 16) | 0
= 0x8C000000  | 0x01000000 | 0x000A0000 | 0
= 0x8D0A0000
```

**Word 3: `0x25080020` -- `ADDIU $t0, $t0, 32`**

Advance pointer by one cache line (32 bytes).

```
I-type: op=0x09, rs=8($t0), rt=8($t0), imm=32(0x20)
(0x09 << 26) | (8 << 21) | (8 << 16) | 0x0020
= 0x24000000  | 0x01000000 | 0x00080000 | 0x0020
= 0x25080020
```

**Word 4: `0x1509FFFD` -- `BNE $t0, $t1, -3`**

Branch back to word 2 (the LW) if the pointer has not reached the end.

```
offset = target_instr - (branch_instr + 1) = 2 - (4 + 1) = -3
-3 in 16-bit two's complement = 0xFFFD

I-type: op=0x05, rs=8($t0), rt=9($t1), imm=0xFFFD
(0x05 << 26) | (8 << 21) | (9 << 16) | 0xFFFD
= 0x14000000  | 0x01000000 | 0x00090000 | 0xFFFD
= 0x1509FFFD
```

**Word 5: `0x00000000` -- `NOP`**

Branch delay slot.

**Word 6: `0x7000003F` -- `SDBBP`**

Halt CPU. OpenOCD detects this as the CPU entering debug mode.

```
(0x1C << 26) | 0x3F = 0x7000003F
```

**Word 7: `0x00000000` -- `NOP`**

Padding after SDBBP.

### Complete Listing

```
Addr        Hex         Assembly              Purpose
0xa0800000  0x3C088000  lui  $t0, 0x8000      t0 = 0x80000000 (KSEG0 start)
0xa0800004  0x3C098002  lui  $t1, 0x8002      t1 = 0x80020000 (start + 128KB)
0xa0800008  0x8D0A0000  lw   $t2, 0($t0)      LOOP: read KSEG0 (evicts dirty line)
0xa080000c  0x25080020  addiu $t0, $t0, 32    advance by cache line size
0xa0800010  0x1509FFFD  bne  $t0, $t1, -3     loop if not at end
0xa0800014  0x00000000  nop                   delay slot
0xa0800018  0x7000003F  sdbbp                 halt -- signal completion
0xa080001c  0x00000000  nop                   padding
```

---

## Trampoline 2: XOR Checksum Program (14 Words)

**Purpose**: Compute a 32-bit XOR checksum of the loaded binary by running on the CPU at 560 MHz, avoiding the 3.5 KB/s PRACC read bottleneck. Store the result in RAM for OpenOCD to read back (a single 4-byte read).

**Location in code**: `make_checksum_program()` in `mr18_flash.py`

**Runs at**: `TRAMPOLINE_ADDR` (`0xA0800000`, KSEG1 uncached)

The program XORs every 32-bit word from `start_kseg1` to `end_kseg1` and stores the result at `TRAMPOLINE_ADDR + 0x40`.

### Word-by-Word Encoding

Using example values: `start = 0xA005FC00`, binary size = 6,931,053 bytes, `end = 0xA005FC00 + (6931053 // 4) * 4 = 0xA06FBE6C`.

**Word 0: `0x3C08A005` -- `LUI $t0, 0xA005`**

Load upper half of start address.

```
I-type: op=0x0F, rs=0, rt=8($t0), imm=0xA005
(0x0F << 26) | (0 << 21) | (8 << 16) | 0xA005
= 0x3C08A005

Result: $t0 = 0xA0050000
```

**Word 1: `0x3508FC00` -- `ORI $t0, $t0, 0xFC00`**

Fill lower half of start address. ORI (not ADDIU) because `0xFC00` has bit 15 set.

```
I-type: op=0x0D, rs=8($t0), rt=8($t0), imm=0xFC00
(0x0D << 26) | (8 << 21) | (8 << 16) | 0xFC00
= 0x3508FC00

Result: $t0 = 0xA005FC00
```

**Word 2: `LUI $t1, hi(end)` -- variable**

Load upper half of end address.

```
I-type: op=0x0F, rs=0, rt=9($t1), imm=hi(end_addr)

For end=0xA06FBE6C: hi=0xA06F
(0x0F << 26) | (0 << 21) | (9 << 16) | 0xA06F
= 0x3C09A06F
```

**Word 3: `ORI $t1, $t1, lo(end)` -- variable**

Fill lower half of end address.

```
I-type: op=0x0D, rs=9($t1), rt=9($t1), imm=lo(end_addr)

For end=0xA06FBE6C: lo=0xBE6C
(0x0D << 26) | (9 << 21) | (9 << 16) | 0xBE6C
= 0x3529BE6C
```

**Word 4: `0x240A0000` -- `ADDIU $t2, $zero, 0`**

Zero the XOR accumulator. `ADDIU $t2, $zero, 0` is equivalent to `move $t2, $zero` or `li $t2, 0`.

```
I-type: op=0x09, rs=0($zero), rt=10($t2), imm=0
(0x09 << 26) | (0 << 21) | (10 << 16) | 0
= 0x240A0000
```

**Word 5: `0x8D0B0000` -- `LW $t3, 0($t0)` [LOOP HEAD]**

Load one word from the binary (via KSEG1 uncached—reads physical RAM directly).

```
I-type: op=0x23, rs=8($t0), rt=11($t3), imm=0
(0x23 << 26) | (8 << 21) | (11 << 16) | 0
= 0x8D0B0000
```

**Word 6: `0x014B5026` -- `XOR $t2, $t2, $t3`**

Accumulate XOR of each word.

```
R-type: op=0, rs=10($t2), rt=11($t3), rd=10($t2), sa=0, funct=0x26
(0 << 26) | (10 << 21) | (11 << 16) | (10 << 11) | (0 << 6) | 0x26
= 0x00000000 | 0x01400000 | 0x000B0000 | 0x00005000 | 0x00000000 | 0x26
= 0x014B5026
```

**Word 7: `0x25080004` -- `ADDIU $t0, $t0, 4`**

Advance pointer by one word (4 bytes).

```
I-type: op=0x09, rs=8($t0), rt=8($t0), imm=4
(0x09 << 26) | (8 << 21) | (8 << 16) | 0x0004
= 0x25080004
```

**Word 8: `0x1509FFFC` -- `BNE $t0, $t1, -4`**

Branch back to word 5 (the LW) if pointer has not reached end.

```
offset = target_instr - (branch_instr + 1) = 5 - (8 + 1) = -4
-4 in 16-bit two's complement = 0xFFFC

I-type: op=0x05, rs=8($t0), rt=9($t1), imm=0xFFFC
(0x05 << 26) | (8 << 21) | (9 << 16) | 0xFFFC
= 0x1509FFFC
```

**Word 9: `0x00000000` -- `NOP`**

Branch delay slot.

**Word 10: `0x3C0CA080` -- `LUI $t4, 0xA080`**

Load upper half of trampoline base (for storing the result). For `TRAMPOLINE_ADDR = 0xA0800000`:

```
I-type: op=0x0F, rs=0, rt=12($t4), imm=0xA080
(0x0F << 26) | (0 << 21) | (12 << 16) | 0xA080
= 0x3C0CA080
```

**Word 11: `0xAD8A0040` -- `SW $t2, 0x40($t4)`**

Store the XOR result at `TRAMPOLINE_ADDR + 0x40`.

```
I-type: op=0x2B, rs=12($t4), rt=10($t2), imm=0x40
(0x2B << 26) | (12 << 21) | (10 << 16) | 0x0040
= 0xAC000000  | 0x01800000 | 0x000A0000 | 0x0040
= 0xAD8A0040
```

Offset `0x40` (64 bytes) is past the 14-word (56-byte) program, preventing self-overwrite.

**Word 12: `0x7000003F` -- `SDBBP`**

Halt CPU. OpenOCD reads the result from `TRAMPOLINE_ADDR + 0x40` after detecting the halt.

**Word 13: `0x00000000` -- `NOP`**

Padding after SDBBP.

### Complete Listing

```
Index  Hex         Assembly                Purpose
 0     0x3C08A005  lui  $t0, 0xA005        start_hi
 1     0x3508FC00  ori  $t0, $t0, 0xFC00   start_lo -> $t0 = 0xA005FC00
 2     0x3C09A06F  lui  $t1, 0xA06F        end_hi (varies with file size)
 3     0x3529BE6C  ori  $t1, $t1, 0xBE6C   end_lo -> $t1 = end addr
 4     0x240A0000  addiu $t2, $zero, 0     accumulator = 0
 5     0x8D0B0000  lw   $t3, 0($t0)        LOOP: load word
 6     0x014B5026  xor  $t2, $t2, $t3      accumulate XOR
 7     0x25080004  addiu $t0, $t0, 4       advance pointer
 8     0x1509FFFC  bne  $t0, $t1, -4       loop if not done
 9     0x00000000  nop                     delay slot
10     0x3C0CA080  lui  $t4, 0xA080        result base (varies with TRAMPOLINE_ADDR)
11     0xAD8A0040  sw   $t2, 0x40($t4)     store result at TRAMPOLINE+0x40
12     0x7000003F  sdbbp                   halt
13     0x00000000  nop                     padding
```

Words 0-3 change per invocation (they encode the start/end addresses of the region to checksum). Words 4-13 are constant. The `cpu_scan_and_fix()` function exploits this by writing words 4-13 once and only updating words 0-3 for each chunk.

---

## Trampoline 3: Launch (2 Words + NOPs)

**Purpose**: Jump to the lzma-loader entry point to start kernel decompression and boot.

**Location in code**: `LAUNCH_TRAMPOLINE` in `mr18_flash.py`

**Runs at**: `TRAMPOLINE_ADDR` (`0xA0800000`, KSEG1 uncached)

### Word-by-Word Encoding

**Word 0: `0x08018000` -- `J 0xA0060000`**

Jump to the lzma-loader entry point at KSEG1 address `0xA0060000`.

```
target26 = (0xA0060000 >> 2) & 0x3FFFFFF
         = 0x28018000 & 0x03FFFFFF
         = 0x00018000

J-type: op=0x02, target26=0x00018000
(0x02 << 26) | 0x00018000
= 0x08000000  | 0x00018000
= 0x08018000
```

Verification that the effective address is correct:

```
PC at J instruction = 0xA0800000
PC+4 = 0xA0800004
PC+4[31:28] = 0xA

Effective jump address = (0xA << 28) | (0x00018000 << 2)
                       = 0xA0000000 | 0x00060000
                       = 0xA0060000  [correct]
```

**Word 1: `0x00000000` -- `NOP`**

Jump delay slot. The J instruction always executes the next instruction (the delay slot) before transferring control to the target.

**Words 2-9: `0x00000000` -- `NOP` (x8)**

Padding. The trampoline is always written as 10 words to maintain a consistent size with the other trampolines.

### Complete Listing

```
Index  Hex         Assembly             Purpose
 0     0x08018000  j 0xa0060000         jump to lzma-loader entry
 1     0x00000000  nop                  delay slot
 2-9   0x00000000  nop (x8)             padding
```

---

## The FLUSH_TRAMPOLINE (CACHE Instruction Variant, 10 Words)

This is the alternative cache flush that uses privileged CACHE instructions instead of LW-based eviction. It is used for the belt-and-suspenders post-load flush but not for the critical pre-load flush (which uses `D_CACHE_FLUSH_TRAMPOLINE` with plain LW).

```
Index  Hex         Assembly                          Purpose
 0     0x3C088000  lui  $t0, 0x8000                  t0 = 0x80000000
 1     0x3C098000  lui  $t1, 0x8000                  t1 = 0x80000000 (base for ORI)
 2     0x35298000  ori  $t1, $t1, 0x8000             t1 = 0x80008000 (base + 32KB)
 3     0xBD000000  cache 0x00, 0($t0)                I-cache Index Invalidate
 4     0xBD010000  cache 0x01, 0($t0)                D-cache Index WB Invalidate
 5     0x25080020  addiu $t0, $t0, 32                advance by line size
 6     0x1509FFFC  bne  $t0, $t1, -4                 loop (branch to word 3)
 7     0x00000000  nop                               delay slot
 8     0x7000003F  sdbbp                             halt
 9     0x00000000  nop                               padding
```

Note that `FLUSH_TRAMPOLINE` sweeps only 32 KB (`0x80000000` to `0x80008000`) because CACHE index operations address individual cache lines directly—one invocation per line flushes that specific line regardless of which way it is in. 32 KB / 32 bytes per line = 1024 index iterations, covering all 256 sets x 4 ways. The ORI at word 2 uses `0x8000` (bit 15 set), which would break with ADDIU (sign extension to `-32768`), hence ORI.

The BNE at word 6 computes: offset = 3 - (6+1) = -4 = 0xFFFC.

---

## verify_asm.py

Location: `jtag/verify_asm.py`

This script independently verifies every hand-encoded instruction word in the project. It does two things:

1. **Manual bit-field calculation**: For each instruction, it shows the encoding arithmetic step by step (opcode shift, register field placement, immediate masking) and compares the computed 32-bit value against the expected constant from `mr18_flash.py`.

2. **Capstone cross-check**: Feeds the raw instruction bytes to [Capstone](https://www.capstone-engine.org/) (a disassembly framework) in MIPS32 big-endian mode and prints the disassembly. This provides an independent ground truth—if our hand-encoded `0x1509FFFC` disassembles as `bne $t0, $t1, -4`, the encoding is correct.

The script covers all three trampolines (D-cache flush, XOR checksum, launch) and uses helper functions that mirror the encoding formulas:

```python
def R(op, rs, rt, rd, sa, funct):
    return (op<<26)|(rs<<21)|(rt<<16)|(rd<<11)|(sa<<6)|funct

def I(op, rs, rt, imm):
    return (op<<26)|(rs<<21)|(rt<<16)|(imm & 0xFFFF)

def J(op, target26):
    return (op<<26)|(target26 & 0x3FFFFFF)
```

Run it with:

```sh
cd jtag/
python3 verify_asm.py
```

All checks should print `[pass]`. Any `[fail]` indicates a mismatch between the encoding formula and the hardcoded constant.

## Bug 12: BEQ vs BNE Encoding Error

Bug 12 was a single-bit encoding error in the D-cache flush trampoline's branch instruction. The original code used opcode `0x04` (BEQ—Branch if Equal) instead of `0x05` (BNE—Branch if Not Equal):

```
BEQ: opcode = 0x04 = 000100 (binary)
BNE: opcode = 0x05 = 000101 (binary)
                            ^ single bit difference
```

With BEQ, the loop branched when `$t0 == $t1` (the termination condition) instead of when `$t0 != $t1` (the continuation condition). The loop body executed exactly once (on the first iteration, `$t0 != $t1`, so BEQ fell through), flushing only a single cache line instead of the full cache. The flush appeared to succeed (SDBBP was reached) but left almost all dirty Cisco lines in the cache.

The fix was changing one bit: `0x04` to `0x05` in the opcode field.

This bug was caught by `verify_asm.py`'s Capstone cross-check, which disassembled the instruction as `beq` instead of the intended `bne`. The lesson: always verify hand-encoded instructions with an independent disassembler.
