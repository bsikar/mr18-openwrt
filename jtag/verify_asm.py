#!/usr/bin/env python3
"""
Verify our hand-encoded MIPS big-endian machine code against:
  1. Manual bit-field calculation (showing the work)
  2. Capstone disassembly (independent ground truth)

This documents every MIPS instruction encoding used in mr18_flash.py
so readers can follow the arithmetic themselves.
"""
import struct
import capstone

# -- MIPS instruction encoding helpers -----------------------------------------
#
# MIPS32 has three instruction formats:
#
#  R-type: [op:6][rs:5][rt:5][rd:5][sa:5][funct:6]
#  I-type: [op:6][rs:5][rt:5][imm:16]
#  J-type: [op:6][target:26]
#
# All fields are big-endian (MSB first).
#
# Key opcodes used here:
#   0x0F = LUI    (I-type, rs=0)
#   0x0D = ORI    (I-type)
#   0x09 = ADDIU  (I-type)
#   0x04 = BEQ    (I-type, but used as BNE below)
#   0x05 = BNE    (I-type)
#   0x23 = LW     (I-type)
#   0x2B = SW     (I-type)
#   0x00 = special (R-type), funct=0x26 -> XOR
#   0x02 = J      (J-type)
#   0x2F = CACHE  (I-type, special encoding)
#   0x1C = SPECIAL2 -- not used here
#   SDBBP: opcode 0x1C (SPECIAL2), funct 0x3F -> 0x7000003F

def R(op, rs, rt, rd, sa, funct):
    return (op<<26)|(rs<<21)|(rt<<16)|(rd<<11)|(sa<<6)|funct

def I(op, rs, rt, imm):
    return (op<<26)|(rs<<21)|(rt<<16)|(imm & 0xFFFF)

def J(op, target26):
    return (op<<26)|(target26 & 0x3FFFFFF)

def hi(a): 
    return (a >> 16) & 0xFFFF

def lo(a): 
    return a & 0xFFFF

def signed_lo(a):
    v = a & 0xFFFF
    return v - 0x10000 if v >= 0x8000 else v

# Register numbers
ZERO, T0, T1, T2, T3, T4 = 0, 8, 9, 10, 11, 12

def disasm(words, base_addr):
    """Disassemble a list of 32-bit big-endian words using capstone."""
    raw = b"".join(struct.pack(">I", w) for w in words)
    md = capstone.Cs(capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS32 | capstone.CS_MODE_BIG_ENDIAN)
    md.detail = False
    results = []
    for insn in md.disasm(raw, base_addr):
        results.append(f"  0x{insn.address:08x}:  {insn.mnemonic:<10} {insn.op_str}")
    return results

def check(name, computed, expected=None):
    if expected is None:
        print(f"  {name:<45} = 0x{computed:08x}")
        return True
    ok = "[pass]" if computed == expected else f"[fail]  expected 0x{expected:08x}"
    print(f"  {name:<45} = 0x{computed:08x}  {ok}")
    return computed == expected

# ===============================================================================
print("=" * 70)
print("XOR CHECKSUM PROGRAM  (14 words, runs at TRAMPOLINE_ADDR = 0xa0200000)")
print("=" * 70)
print()

LOAD_KSEG1  = 0xa005fc00
FILE_SIZE   = 6931053          # actual byte count of initramfs binary
TRAMPOLINE  = 0xa0200000
RESULT_OFF  = 0x40

end_addr    = LOAD_KSEG1 + (FILE_SIZE // 4) * 4  # word-aligned end

print(f"  start_kseg1 = 0x{LOAD_KSEG1:08x}")
print(f"  file_size   = {FILE_SIZE} bytes ({FILE_SIZE//1024} KB)")
print(f"  end_addr    = start + (size // 4) * 4")
print(f"              = 0x{LOAD_KSEG1:08x} + 0x{(FILE_SIZE//4)*4:08x}")
print(f"              = 0x{end_addr:08x}")
print(f"  result_addr = TRAMPOLINE + 0x{RESULT_OFF:02x} = 0x{TRAMPOLINE+RESULT_OFF:08x}")
print()
print("Encoding each instruction (showing bit-field arithmetic):")
print()

# Instruction 0: LUI t0, hi(start_kseg1)
#   I-type: op=0x0F(15), rs=0, rt=T0(8), imm=hi(start_kseg1)
#   [001111][00000][01000][imm16]
w0 = I(0x0F, 0, T0, hi(LOAD_KSEG1))
print(f"  [0] LUI t0, 0x{hi(LOAD_KSEG1):04x}")
print(f"      op=0x0F rs=0 rt=8 imm=0x{hi(LOAD_KSEG1):04x}")
print(f"      (0x0F<<26)|(0<<21)|(8<<16)|0x{hi(LOAD_KSEG1):04x}")
check("[0] LUI t0, hi(start)", w0, 0x3C08A005)

print()
# Instruction 1: ORI t0, t0, lo(start_kseg1)
#   I-type: op=0x0D(13), rs=T0(8), rt=T0(8), imm=lo(start_kseg1)
w1 = I(0x0D, T0, T0, lo(LOAD_KSEG1))
print(f"  [1] ORI t0, t0, 0x{lo(LOAD_KSEG1):04x}")
print(f"      op=0x0D rs=8 rt=8 imm=0x{lo(LOAD_KSEG1):04x}")
check("[1] ORI t0, t0, lo(start)", w1, 0x3508FC00)

print()
# Instruction 2: LUI t1, hi(end_addr)
w2 = I(0x0F, 0, T1, hi(end_addr))
print(f"  [2] LUI t1, 0x{hi(end_addr):04x}")
check("[2] LUI t1, hi(end)", w2)

print()
# Instruction 3: ORI t1, t1, lo(end_addr)
w3 = I(0x0D, T1, T1, lo(end_addr))
print(f"  [3] ORI t1, t1, 0x{lo(end_addr):04x}")
print(f"      end_addr=0x{end_addr:08x}  hi=0x{hi(end_addr):04x}  lo=0x{lo(end_addr):04x}")
check("[3] ORI t1, t1, lo(end)", w3)

print()
# Instruction 4: ADDIU t2, $0, 0  (t2 = accumulator = 0)
#   I-type: op=0x09, rs=0, rt=T2(10), imm=0
w4 = I(0x09, 0, T2, 0)
print(f"  [4] ADDIU t2, $0, 0   (zero accumulator)")
print(f"      op=0x09 rs=0 rt=10 imm=0")
check("[4] ADDIU t2, $0, 0", w4, 0x240A0000)

print()
# Instruction 5: LW t3, 0(t0)   <- LOOP target
#   I-type: op=0x23(35), rs=T0(8), rt=T3(11), imm=0
w5 = I(0x23, T0, T3, 0)
print(f"  [5] LW t3, 0(t0)   <- loop head")
print(f"      op=0x23 rs=8 rt=11 imm=0")
check("[5] LW t3, 0(t0)", w5, 0x8D0B0000)

print()
# Instruction 6: XOR t2, t2, t3
#   R-type: op=0, rs=T2(10), rt=T3(11), rd=T2(10), sa=0, funct=0x26
w6 = R(0, T2, T3, T2, 0, 0x26)
print(f"  [6] XOR t2, t2, t3")
print(f"      R-type: op=0 rs=10 rt=11 rd=10 sa=0 funct=0x26")
print(f"      (0<<26)|(10<<21)|(11<<16)|(10<<11)|(0<<6)|0x26")
check("[6] XOR t2, t2, t3", w6, 0x014B5026)

print()
# Instruction 7: ADDIU t0, t0, 4
w7 = I(0x09, T0, T0, 4)
print(f"  [7] ADDIU t0, t0, 4   (advance pointer)")
check("[7] ADDIU t0, t0, 4", w7, 0x25080004)

print()
# Instruction 8: BNE t0, t1, -4  (branch back to instruction 5)
#   I-type: op=0x05, rs=T0(8), rt=T1(9), imm=offset
#   Branch offset is relative to PC+4 (next instruction), in words.
#   Target = instruction 5, current PC = instruction 8.
#   offset_words = 5 - (8+1) = -4
#   imm field = -4 as signed 16-bit = 0xFFFC
w8 = I(0x05, T0, T1, 0xFFFC)
print(f"  [8] BNE t0, t1, -4   (branch offset = 5 - (8+1) = -4 words)")
print(f"      MIPS branch offset = (target_instr - (branch_instr + 1))")
print(f"      = instr[5] - (instr[8] + 1) = 5 - 9 = -4")
print(f"      imm = -4 as unsigned 16-bit = 0x{(-4)&0xFFFF:04x}")
check("[8] BNE t0, t1, -4", w8, 0x1509FFFC)

print()
# Instruction 9: NOP (delay slot)
w9 = 0x00000000
print(f"  [9] NOP (branch delay slot)")
check("[9] NOP", w9, 0x00000000)

print()
# Instruction 10: LUI t4, hi(TRAMPOLINE)
w10 = I(0x0F, 0, T4, hi(TRAMPOLINE))
print(f"  [10] LUI t4, 0x{hi(TRAMPOLINE):04x}   (result base address)")
print(f"       TRAMPOLINE=0x{TRAMPOLINE:08x}  hi=0x{hi(TRAMPOLINE):04x}")
check("[10] LUI t4, hi(TRAMPOLINE)", w10, 0x3C0CA020)

print()
# Instruction 11: SW t2, 0x40(t4)
#   I-type: op=0x2B(43), rs=T4(12), rt=T2(10), imm=0x40
w11 = I(0x2B, T4, T2, RESULT_OFF)
print(f"  [11] SW t2, 0x{RESULT_OFF:02x}(t4)   (store XOR result)")
print(f"       op=0x2B rs=12 rt=10 imm=0x{RESULT_OFF:02x}")
check("[11] SW t2, 0x40(t4)", w11, 0xAD8A0040)

print()
# Instruction 12: SDBBP
#   Encoding: 0x7000003F  (SPECIAL2 opcode=0x1C, funct=0x3F)
#   [011100][00000000000000000000][111111]
#   = (0x1C<<26) | 0x3F = 0x70000000 | 0x3F = 0x7000003F
w12 = (0x1C << 26) | 0x3F
print(f"  [12] SDBBP  (software debug breakpoint)")
print(f"       SPECIAL2 opcode = 0x1C, funct = 0x3F")
print(f"       (0x1C<<26)|0x3F = 0x{0x1C<<26:08x}|0x3F = 0x{w12:08x}")
check("[12] SDBBP", w12, 0x7000003F)

print()
w13 = 0x00000000
print(f"  [13] NOP")
check("[13] NOP", w13, 0x00000000)

XOR_PROG = [w0, w1, w2, w3, w4, w5, w6, w7, w8, w9, w10, w11, w12, w13]
print()
print("Capstone disassembly (independent verification):")
for line in disasm(XOR_PROG, TRAMPOLINE):
    print(line)

# ===============================================================================
print()
print("=" * 70)
print("LAUNCH TRAMPOLINE  (2 meaningful words + 8 NOPs)")
print("=" * 70)
print()

ENTRY_KSEG1 = 0xa0060000
# J-type: op=0x02, target26 = (addr >> 2) & 0x3FFFFFF
# But: the top 4 bits of the target come from PC+4[31:28].
# For KSEG1 address 0xa0060000:
#   PC+4 will be TRAMPOLINE+4 = 0xa0200004
#   PC+4[31:28] = 0xA = 1010
#   target26 = (0xa0060000 >> 2) & 0x3FFFFFF
#            = 0x28018000 & 0x3FFFFFF = 0x0018000
# Reconstructed: (0xA << 28) | (0x18000 << 2) = 0xa0060000 [pass]
target26 = (ENTRY_KSEG1 >> 2) & 0x3FFFFFF
wj = J(0x02, target26)
print(f"  J 0x{ENTRY_KSEG1:08x}")
print(f"  J-type encoding: op=0x02, target26 = (addr >> 2) & 0x3FFFFFF")
print(f"  target26 = (0x{ENTRY_KSEG1:08x} >> 2) & 0x3FFFFFF")
print(f"           = 0x{ENTRY_KSEG1>>2:08x} & 0x03FFFFFF")
print(f"           = 0x{target26:07x}")
print(f"  word     = (0x02 << 26) | 0x{target26:07x} = 0x{wj:08x}")
print()
print(f"  Verification: PC at J is 0x{TRAMPOLINE:08x}")
print(f"  PC+4 = 0x{TRAMPOLINE+4:08x}  ->  top nibble = 0x{(TRAMPOLINE+4)>>28:X}")
print(f"  Jump target = (0x{(TRAMPOLINE+4)>>28:X} << 28) | (0x{target26:07x} << 2)")
print(f"              = 0x{((TRAMPOLINE+4)&0xF0000000)|(target26<<2):08x}  [pass]" if ((TRAMPOLINE+4)&0xF0000000)|(target26<<2) == ENTRY_KSEG1 else "  [fail] mismatch!")
check("J 0xa0060000", wj, 0x08018000)

LAUNCH = [wj, 0, 0, 0, 0, 0, 0, 0, 0, 0]
print()
print("Capstone disassembly:")
for line in disasm(LAUNCH[:2], TRAMPOLINE):
    print(line)

# ===============================================================================
print()
print("=" * 70)
print("FLUSH TRAMPOLINE  (10 words -- documented even though we removed it)")
print("=" * 70)
print()
print("  Purpose: write back + invalidate D-cache before KSEG1 reads.")
print("  Sweeps KSEG0 addresses 0x80000000..0x80003FE0 (step 32 bytes).")
print("  Each iteration: cache 0x00 (I-cache Index_Invalidate) +")
print("                  cache 0x01 (D-cache Index_WB_Invalidate)")
print("  After loop: SDBBP to signal halt to OpenOCD.")
print()
print("  CACHE instruction encoding (I-type variant):")
print("  [101111][base:5][op:5][offset:16]")
print("  op=0: I-cache Index_Invalidate   -> 0xBD000000 (base=t0, offset=0)")
print("  op=1: D-cache Index_WB_Invalidate -> 0xBD010000")
print()

# Flush trampoline words (from mr18_flash.py)
FLUSH = [
    0x3C088000,  # lui  t0, 0x8000
    0x3C098000,  # lui  t1, 0x8000
    0x25294000,  # addiu t1, t1, 0x4000
    0xBD000000,  # cache 0x00, 0(t0)  I-cache Index_Invalidate
    0xBD010000,  # cache 0x01, 0(t0)  D-cache Index_WB_Invalidate
    0x25080020,  # addiu t0, t0, 32
    0x1509FFFC,  # bne   t0, t1, -4
    0x00000000,  # nop
    0x7000003F,  # sdbbp
    0x00000000,  # nop
]

print("Verification of each word:")
check("LUI t0, 0x8000",          I(0x0F, 0, T0, 0x8000), 0x3C088000)
check("LUI t1, 0x8000",          I(0x0F, 0, T1, 0x8000), 0x3C098000)
# ADDIU t1, t1, 0x4000: note 0x4000 < 0x8000 so no sign-extension issue
check("ADDIU t1, t1, 0x4000",    I(0x09, T1, T1, 0x4000), 0x25294000)
# CACHE encoding: op=0x2F, base=T0, cache_op=0, offset=0
# Bits: [101111][01000][00000][0000000000000000] = 0xBD000000
cache_i_inv  = (0x2F<<26)|(T0<<21)|(0<<16)|0
cache_d_wbi  = (0x2F<<26)|(T0<<21)|(1<<16)|0
check("CACHE 0, 0(t0) [I-inv]",  cache_i_inv, 0xBD000000)
check("CACHE 1, 0(t0) [D-WBI]",  cache_d_wbi, 0xBD010000)
check("ADDIU t0, t0, 32",        I(0x09, T0, T0, 32), 0x25080020)
check("BNE t0, t1, -4",          I(0x05, T0, T1, 0xFFFC), 0x1509FFFC)
check("NOP",                     0x00000000, 0x00000000)
check("SDBBP",                   (0x1C<<26)|0x3F, 0x7000003F)
check("NOP",                     0x00000000, 0x00000000)

print()
print("Capstone disassembly:")
for line in disasm(FLUSH, TRAMPOLINE):
    print(line)

print()
print("=" * 70)
print("ALL CHECKS COMPLETE")
print("=" * 70)
