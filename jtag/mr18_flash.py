#!/usr/bin/env python3
"""
MR18 OpenWrt JTAG installation automation.

Timing attack: halt CPU during ~2s bootloader window before Linux boots
and repurposes TDO/disables JTAG probing.

Flow:
  1. Start OpenOCD server
  2. Power off MR18 via PSU
  3. Power on MR18 via PSU
  4. Immediately hit OpenOCD telnet with arp_init + halt
  5. Load initramfs kernel to RAM
  6. Resume at kernel entry point
  7. User flashes sysupgrade via web UI at 192.168.1.1
"""
import subprocess
import socket
import time
import sys
import os
import re
import threading
import serial

# Paths -- relative to project root (parent of jtag/)
_SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
INITRAMFS    = os.path.join(_PROJECT_DIR, "firmware", "openwrt-25.12.0-ath79-nand-meraki_mr18-initramfs-kernel.bin")
SYSUPGRADE   = os.path.join(_PROJECT_DIR, "firmware", "openwrt-25.12.0-ath79-nand-meraki_mr18-squashfs-sysupgrade.bin")
PSU_PIPE     = "/tmp/scpi_pipe"
REPL_LOG     = "/tmp/scpi_repl.log"

# Network -- host NIC connected directly to MR18 ethernet
HOST_NIC     = "enx6c1ff71fee83"
HOST_IP      = "192.168.1.2/24"
OPENWRT_IP   = "192.168.1.1"

# AR9344/QCA9557 GPIO registers (KSEG1 uncached, direct MMIO via JTAG mww)
# Kept for reference / diagnostics; no longer used for failsafe trigger.
GPIO_OE   = 0xb8040000   # output-enable register: bit=1 -> drive pin, bit=0 -> input
GPIO_IN   = 0xb8040004   # read-only actual pin state (reflects physical pin regardless of OE)
GPIO_OUT  = 0xb8040008   # current output value register (read-only shadow of driven output)
GPIO_SET  = 0xb804000c   # write 1 to drive pin HIGH (set)
GPIO_CLR  = 0xb8040010   # write 1 to drive pin LOW (clear)
GPIO_FUNC = 0xb8040028   # AR934x alternate-function override register (bit set = alt fn)
RESET_GPIO_BIT = 1 << 17   # 0x00020000

# Failsafe mode timing after kernel launch:
#   ~3-5s  LZMA decompresses kernel image into DRAM
#   ~5-8s  kernel init, platform probe, gpio-keys driver sets GPIO17 as INPUT
#   ~8-12s preinit starts (/etc/preinit sourcing /lib/preinit/*.sh)
#   ~10-18s failsafe button check window (~3s polling loop in 30_failsafe_wait)
#   ~20-40s failsafe network stack up (192.168.1.1 static, telnetd on port 23)

# ESP-Prog UART interface (FT2232H interface B, /dev/ttyUSB4).
# The EN pin is driven by the FT2232H's RTS line through an NPN transistor:
#   ser.rts = True  -> transistor conducts -> EN pin pulled LOW -> GPIO17 LOW
#   ser.rts = False -> transistor off      -> EN released (pull-up -> HIGH)
# This is the same auto-reset circuit esptool.py uses to reset ESP32 boards.
# Wire: ESP-Prog UART connector EN pin -> reset button non-GND pad (GPIO17 net).
# CAUTION: add a 100ohm series resistor on the EN wire in case the MR18's reset
# supervisor IC has a push-pull HIGH output -- limits current if both fight.
ESPPROG_UART      = "/dev/ttyUSB4"
# MR18 UART (console) is carried on the same FT2232H interface B as the EN pin.
# ttyUSB4 carries TXD0->MR18_RX, RXD0<-MR18_TX, and RTS->EN transistor.
# We read MR18 console output from the same ser object used for EN control.
# Timing: the initramfs binary is 6.9MB compressed -> ~26MB decompressed.
# At ~2 MB/s LZMA output on 560MHz MIPS, decompression alone takes ~13s.
# Kernel init adds another ~5-8s before preinit starts.
# Preinit failsafe window: roughly t=18-28s after lzma-loader launches.
# Strategy: assert EN from t=2s and hold for 40s -- blanket the entire window.
FAILSAFE_EN_DELAY = 2.0    # seconds after kernel launch before asserting EN
FAILSAFE_EN_HOLD  = 40.0   # seconds to hold EN LOW (covers full preinit window)

# Addresses (from OpenWrt MR18 JTAG wiki)
LOAD_ADDR    = "0xa005FC00"   # KSEG1: uncached writes go straight to physical RAM (no cache flush needed)
ENTRY_ADDR   = "0x80060000"   # decompressor entry
ENTRY_KSEG1  = "0xa0060000"  # KSEG1 (uncached) view of same physical entry
LOAD_KSEG1   = "0xa005FC00"  # KSEG1 load address (same as LOAD_ADDR)

# Trampoline region in KSEG1 (uncached) -- used for flush/checksum/launch programs.
# MUST be above the binary end (~0xa06fbe6d, physical ~7MB) so rewrites of the binary
# do not clobber the program.  0xa0800000 = physical 8MB, 1MB above binary end, safe.
TRAMPOLINE_ADDR = "0xa0800000"

# Flush-only trampoline: cache flush loop ending with SDBBP (software debug
# breakpoint) instead of jumping to the decompressor.  Used to flush both caches to
# RAM *before* we verify the loaded binary via KSEG1 uncached reads, so the reads
# reflect actual physical RAM content rather than stale D-cache data.
FLUSH_TRAMPOLINE = [
    0x3C088000,  # lui  t0, 0x8000
    0x3C098000,  # lui  t1, 0x8000
    0x35298000,  # ori   t1, t1, 0x8000  <- 32KB covers all 4 ways (ori zero-extends; addiu would sign-extend 0x8000 -> -32768)
    0xBD000000,  # cache 0x00, 0(t0)  -> I-cache Index_Invalidate
    0xBD010000,  # cache 0x01, 0(t0)  -> D-cache Index_WB_Invalidate
    0x25080020,  # addiu t0, t0, 32
    0x1509FFFC,  # bne   t0, t1, -4
    0x00000000,  # nop
    0x7000003F,  # sdbbp -> CPU enters debug mode; OpenOCD detects as halt
    0x00000000,  # nop
]

# D-cache flush trampoline -- runs BEFORE load_image.
#
# Problem: the Cisco Nandloader writes the Cisco kernel to physical 0x5FC00 via
# KSEG0 (cached, write-back, write-allocate on AR9344).  This fills D-cache lines
# at physical 0x5FC00+ with dirty Cisco data (DRAM lags behind cache).
#
# We then load our OpenWrt binary to the same physical range via KSEG1 (uncached,
# bypasses D-cache -> goes straight to DRAM).  But the D-cache still holds dirty
# Cisco lines for those physical addresses.
#
# The lzma-loader copies itself to KSEG0 0x82800000, then reads LZMA stream data
# via KSEG0.  During any KSEG0 read loop that sweeps physical addresses, dirty
# Cisco lines are EVICTED WITH WRITE-BACK -- overwriting our freshly loaded OpenWrt
# binary in DRAM!  Result: lzma-loader reads Cisco bytes -> "data error!".
#
# Fix: run this flush BEFORE load_image.  The dirty Cisco lines are written back
# to DRAM (harmless -- we are about to overwrite that DRAM anyway), and the D-cache
# is fully cleaned.  load_image then writes OpenWrt to clean DRAM.  At launch, the
# D-cache has no stale entries covering our binary range.
#
# Instruction breakdown:
#   0  lui  t0, 0x8000   -> t0 = 0x80000000 (start of KSEG0)
#   1  lui  t1, 0x8002   -> t1 = 0x80020000 (start + 128KB = 4x D-cache)
#   2  lw   t2, 0(t0)    <- LOOP: KSEG0 read, evicts one dirty D-cache line
#   3  addiu t0, t0, 32  -> advance by cache line size (32 bytes)
#   4  bne  t0, t1, -3   -> branch to word 2 while t0 < end
#   5  nop  (delay slot)
#   6  sdbbp              <- CPU enters debug mode; OpenOCD detects halt
#   7  nop
# SDBBP at word 6 = TRAMPOLINE_ADDR + 0x18
D_CACHE_FLUSH_TRAMPOLINE = [
    0x3C088000,  # lui  t0, 0x8000
    0x3C098002,  # lui  t1, 0x8002
    0x8D0A0000,  # lw   t2, 0(t0)    <- LOOP
    0x25080020,  # addiu t0, t0, 32
    0x1509FFFD,  # bne  t0, t1, -3
    0x00000000,  # nop
    0x7000003F,  # sdbbp
    0x00000000,  # nop
]

# Minimal launch trampoline: jump to lzma-loader entry (KSEG1, uncached).
# D-cache was already flushed before load_image, so no stale Cisco lines remain.
LAUNCH_TRAMPOLINE = [
    0x08018000,  # j 0xa0060000 (KSEG1 lzma-loader entry, uncached)
    0x00000000,  # nop (j delay slot)
    0x00000000, 0x00000000, 0x00000000, 0x00000000,
    0x00000000, 0x00000000, 0x00000000, 0x00000000,
]

# Checksum result is stored at TRAMPOLINE_ADDR + this offset (past the 14-word program)
CHECKSUM_RESULT_OFFSET = 0x40  # = 64 bytes, well past 14 words (56 bytes)

# OpenOCD telnet
OCD_HOST     = "127.0.0.1"
OCD_PORT     = 4444

# EJTAG control register values
EJTAG_IR       = "0x0a"
EJTAG_PROBEN   = (1 << 15)  # 0x8000
EJTAG_JTAGBRK  = (1 << 12)  # 0x1000
EJTAG_BRKST    = (1 << 11)  # 0x0800
EJTAG_BIT3     = (1 << 3)   # 0x0008  always set
EJTAG_HALT_WR  = EJTAG_PROBEN | EJTAG_JTAGBRK | EJTAG_BIT3  # 0x9008


# -- PSU ----------------------------------------------------------------------

def psu(cmd, delay=0.4):
    """Inject a command into the persistent scpi-repl via named pipe."""
    with open(PSU_PIPE, 'a') as f:   # 'a' = O_APPEND, same as >> in bash
        f.write(cmd + '\n')
    time.sleep(delay)


def kill_repl():
    """Kill any running scpi-repl and tail feeder.

    The repl applies a safe state on exit: PSU output disabled, 0V, 0A.
    """
    subprocess.run(["pkill", "-f", "scpi-repl"], capture_output=True)
    subprocess.run(["pkill", "-f", f"tail.*{PSU_PIPE}"], capture_output=True)
    time.sleep(1.0)


def start_repl() -> tuple:
    """Start the scpi-repl and wait until it has finished instrument discovery.

    Pattern: tail -f PSU_PIPE | scpi-repl
    Commands are injected via psu() which appends lines to PSU_PIPE.
    Output goes to REPL_LOG so we can poll for the ready prompt.

    Returns (tail_proc, repl_proc) -- both must be terminated on exit.
    """
    # Create the named pipe if it doesn't exist
    if not os.path.exists(PSU_PIPE):
        os.mkfifo(PSU_PIPE)

    log = open(REPL_LOG, "w")
    tail_proc = subprocess.Popen(
        ["tail", "-f", PSU_PIPE],
        stdout=subprocess.PIPE,
    )
    repl_proc = subprocess.Popen(
        ["scpi-repl"],
        stdin=tail_proc.stdout,
        stdout=log,
        stderr=log,
    )

    print("[*] Waiting for scpi-repl to initialize (up to 30s) ...")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        time.sleep(1)
        try:
            with open(REPL_LOG) as f:
                content = f.read()
            if "eset>" in content:
                print("[+] scpi-repl ready")
                return tail_proc, repl_proc
        except FileNotFoundError:
            pass

    print("[-] scpi-repl did not reach ready state -- check /tmp/scpi_repl.log")
    return tail_proc, repl_proc


# -- OpenOCD telnet client -----------------------------------------------------

class OCD:
    def __init__(self):
        self.sock = None

    def connect(self, retries=20) -> bool:
        for _ in range(retries):
            try:
                s = socket.socket()
                s.settimeout(2.0)
                s.connect((OCD_HOST, OCD_PORT))
                self.sock = s
                self._drain()   # consume banner + first prompt
                return True
            except OSError:
                time.sleep(0.2)
        return False

    def _drain(self, timeout=1.0):
        data = b""
        self.sock.settimeout(timeout)
        try:
            while True:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                data += chunk
                if data.rstrip().endswith(b">"):
                    break
        except socket.timeout:
            pass
        return data.decode(errors="replace")

    def cmd(self, command: str, timeout=5.0) -> str:
        self.sock.sendall((command + "\n").encode())
        return self._drain(timeout=timeout)

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass


# -- OpenOCD process -----------------------------------------------------------

def kill_openocd():
    subprocess.run(["pkill", "-9", "-f", "openocd"], capture_output=True)
    time.sleep(0.5)


def start_openocd() -> subprocess.Popen:
    """
    Start OpenOCD WITH init (so it scans a live TAP and properly examines the target).
    Call this AFTER the MR18 has been powered on and Nandloader is running.
    """
    log = open("/tmp/openocd.log", "w")
    proc = subprocess.Popen(
        [
            "openocd",
            "-f", f"{CFG_DIR}/esp-prog.cfg",
            "-f", f"{CFG_DIR}/mr18.cfg",
            "-c", "init",
        ],
        stdout=log,
        stderr=log,
    )
    return proc


# -- halt helpers -------------------------------------------------------------

def try_halt_highlevel(ocd: OCD) -> bool:
    """Use OpenOCD high-level halt command."""
    resp = ocd.cmd("halt", timeout=2.0)
    print(f"      halt -> {resp.strip()[:120]}")
    return "halted" in resp.lower() or "debug mode" in resp.lower()


def try_halt_ejtag(ocd: OCD) -> bool:
    """
    Raw EJTAG halt sequence.
    Write PROBEN=1 + JTAGBRK=1 to control register, then poll BRKST.
    """
    ocd.cmd(f"irscan ar9344.cpu {EJTAG_IR}")
    wr_val = f"0x{EJTAG_HALT_WR:08x}"
    resp = ocd.cmd(f"drscan ar9344.cpu 32 {wr_val}", timeout=2.0)
    print(f"      EJTAG write {wr_val} -> {resp.strip()}")

    for _ in range(10):
        resp = ocd.cmd("drscan ar9344.cpu 32 0x00000000", timeout=1.0).strip()
        print(f"      EJTAG poll -> {resp}")
        try:
            val = int(resp.split()[-1], 16)
            if val & EJTAG_BRKST:
                print("      BRKST set -> CPU in debug mode!")
                return True
            # Re-assert PROBEN+JTAGBRK if PROBEN cleared
            if not (val & EJTAG_PROBEN):
                ocd.cmd(f"irscan ar9344.cpu {EJTAG_IR}")
                ocd.cmd(f"drscan ar9344.cpu 32 {wr_val}", timeout=1.0)
        except (ValueError, IndexError):
            pass
        time.sleep(0.05)
    return False


def init_tap(ocd: OCD) -> bool:
    """One-shot: scan chain and examine target. Call once per power-cycle."""
    resp = ocd.cmd("jtag arp_init", timeout=3.0)
    print(f"    arp_init -> {resp.strip()[:120]}")
    if "error" in resp.lower() and "tap" in resp.lower():
        return False
    resp = ocd.cmd("ar9344.cpu arp_examine", timeout=2.0)
    print(f"    arp_examine -> {resp.strip()[:120]}")
    return True


def try_halt_once(ocd: OCD) -> bool:
    """Single fast halt attempt: high-level halt + short wait."""
    resp = ocd.cmd("halt", timeout=2.0)
    resp2 = ocd.cmd("wait_halt 300", timeout=1.0)
    combined = resp + resp2
    return "halted" in combined.lower() or "debug mode" in combined.lower()


def examine_and_halt(ocd: OCD) -> bool:
    """Re-examine TAP and attempt halt via both methods."""
    # Re-scan the JTAG chain (works even after power cycle)
    if not init_tap(ocd):
        return False

    # High-level halt first
    if try_halt_highlevel(ocd):
        return True

    # Fall back to raw EJTAG
    return try_halt_ejtag(ocd)


# -- image loading -------------------------------------------------------------

def compute_xor32(data: bytes) -> int:
    """XOR all 32-bit big-endian words (truncated to word boundary)."""
    xor = 0
    for i in range(0, (len(data) // 4) * 4, 4):
        xor ^= int.from_bytes(data[i:i+4], 'big')
    return xor & 0xFFFFFFFF


def make_checksum_program(start_kseg1: int, file_size: int) -> list:
    """Build a 14-word MIPS big-endian XOR-checksum program.

    Runs uncached from TRAMPOLINE_ADDR (KSEG1):
      - reads every 32-bit word from start_kseg1 .. start_kseg1 + aligned(file_size)
      - XORs them all (big-endian, matches load_image + lw)
      - stores the 32-bit result at TRAMPOLINE_ADDR + CHECKSUM_RESULT_OFFSET
      - hits SDBBP so OpenOCD detects the halt

    Instruction breakdown (0-indexed, offsets in words):
      0  lui  t0, hi(start)
      1  ori  t0, t0, lo(start)
      2  lui  t1, hi(end)
      3  ori  t1, t1, lo(end)
      4  addiu t2, $0, 0           <- accumulator = 0
      5  lw    t3, 0(t0)           <- LOOP TARGET
      6  xor   t2, t2, t3
      7  addiu t0, t0, 4
      8  bne   t0, t1, -4          <- branch to instr 5 (offset -4 words from PC+1)
      9  nop   (delay slot)
     10  lui   t4, hi(result_base)
     11  sw    t2, CHECKSUM_RESULT_OFFSET(t4)
     12  sdbbp
     13  nop
    """
    end  = start_kseg1 + (file_size // 4) * 4  # word-aligned end
    base = int(TRAMPOLINE_ADDR, 16)

    def hi(a): return (a >> 16) & 0xFFFF
    def lo(a): return  a        & 0xFFFF

    return [
        (0x3C08 << 16) | hi(start_kseg1),  # lui  t0, hi(start)
        (0x3508 << 16) | lo(start_kseg1),  # ori  t0, t0, lo(start)
        (0x3C09 << 16) | hi(end),           # lui  t1, hi(end)
        (0x3529 << 16) | lo(end),           # ori  t1, t1, lo(end)
        0x240A0000,                          # addiu t2, $0, 0
        0x8D0B0000,                          # lw   t3, 0(t0)      <- LOOP
        0x014B5026,                          # xor  t2, t2, t3
        0x25080004,                          # addiu t0, t0, 4
        0x1509FFFC,                          # bne  t0, t1, -4
        0x00000000,                          # nop
        (0x3C0C << 16) | hi(base),           # lui  t4, hi(result_base)
        (0xAD8A << 16) | CHECKSUM_RESULT_OFFSET,  # sw t2, offset(t4)
        0x7000003F,                          # sdbbp
        0x00000000,                          # nop
    ]


def verify_and_fix(ocd: OCD, file_data: bytes, load_addr: int) -> bool:
    """
    Verify binary in RAM via KSEG1 uncached dump_image reads (bypasses D-cache).
    Rewrite any mismatched 32-bit words via mww to KSEG1 (uncached -> direct RAM).
    Uses 8 KB chunks to minimise PRACC control-error abort probability per chunk,
    and retries each chunk up to MAX_DUMP_RETRIES times before giving up.
    """
    file_size = len(file_data)
    CHUNK = 8192          # small chunks -> fewer PRACC ctrl errors per chunk
    MAX_RETRIES = 4
    bad_words = {}  # byte_offset_from_load -> correct_word_bigendian
    total_chunks = (file_size + CHUNK - 1) // CHUNK

    print(f"[*] Verifying {file_size // 1024} KB at 0x{load_addr:08x} "
          f"({total_chunks} x {CHUNK//1024} KB chunks, up to {MAX_RETRIES} retries each) ...")
    tmpf = "/tmp/verify_chunk.bin"

    for chunk_idx, chunk_off in enumerate(range(0, file_size, CHUNK)):
        chunk_size = min(CHUNK, file_size - chunk_off)
        ram_addr   = load_addr + chunk_off

        # Retry loop for each chunk (PRACC control errors cause occasional aborts)
        ok = False
        for attempt in range(MAX_RETRIES):
            r = ocd.cmd(f"dump_image {tmpf} 0x{ram_addr:08x} {chunk_size}", timeout=60.0)
            got = os.path.getsize(tmpf) if os.path.exists(tmpf) else 0
            if got >= chunk_size:
                ok = True
                break
            print(f"  [!] Chunk {chunk_idx+1}/{total_chunks} 0x{ram_addr:08x} "
                  f"incomplete ({got}/{chunk_size}), retry {attempt+1}/{MAX_RETRIES} ...")
            time.sleep(0.5)

        if not ok:
            print(f"  [-] Chunk 0x{ram_addr:08x} failed after {MAX_RETRIES} retries")
            return False

        with open(tmpf, "rb") as f:
            ram_chunk = f.read()
        ref_chunk = file_data[chunk_off:chunk_off + len(ram_chunk)]
        for word_off in range(0, min(len(ram_chunk), len(ref_chunk)) & ~3, 4):
            ram_w = int.from_bytes(ram_chunk[word_off:word_off + 4], 'big')
            ref_w = int.from_bytes(ref_chunk[word_off:word_off + 4], 'big')
            if ram_w != ref_w:
                bad_words[chunk_off + word_off] = ref_w

        if (chunk_idx + 1) % 100 == 0 or chunk_idx + 1 == total_chunks:
            print(f"  ... {chunk_idx+1}/{total_chunks} chunks read, {len(bad_words)} bad words so far")

    if not bad_words:
        print(f"[+] Binary verified clean ({file_size // 1024} KB, 0 errors) [pass]")
        return True

    print(f"[!] {len(bad_words)} corrupted word(s) -- rewriting via mww (KSEG1) ...")
    for off, word in sorted(bad_words.items()):
        addr = load_addr + off
        # Retry mww until readback confirms correct value
        for _ in range(MAX_RETRIES):
            ocd.cmd(f"mww 0x{addr:08x} 0x{word:08x}")
            r = ocd.cmd(f"mdw 0x{addr:08x}", timeout=5.0)
            m = re.search(r':\s+([0-9a-fA-F]{8})', r)
            if m and int(m.group(1), 16) == word:
                break

    # Final re-check of all corrected words
    still_bad = 0
    for off, expected in sorted(bad_words.items()):
        addr = load_addr + off
        r = ocd.cmd(f"mdw 0x{addr:08x}", timeout=5.0)
        m = re.search(r':\s+([0-9a-fA-F]{8})', r)
        got = int(m.group(1), 16) if m else None
        if got != expected:
            still_bad += 1
    if still_bad:
        print(f"[-] {still_bad} word(s) still bad after rewrite -- aborting")
        return False
    print(f"[+] All {len(bad_words)} word(s) corrected and verified [pass]")
    return True


def cpu_scan_and_fix(ocd: OCD, file_data: bytes, load_addr: int) -> bool:
    """
    Find and fix PRACC write errors using only CPU-executed XOR -- no dump_image.

    For each 8KB chunk the MIPS CPU runs the XOR loop itself.  The only PRACC
    reads are: 4 words (program header per chunk) + 1 word (XOR result).
    Bad chunks are rewritten entirely from file bytes, then re-verified with a
    second CPU XOR.  This eliminates the 'phantom error' problem in verify_and_fix
    where PRACC read errors caused correct words to appear bad and real bad words
    to appear correct.

    Time estimate: ~12 PRACC ops x 847 chunks ~ 60 s scan + ~2 s per bad chunk.
    """
    CHUNK = 8192
    file_size = len(file_data)
    tramp = int(TRAMPOLINE_ADDR, 16)       # 0xa0200000
    result_addr = tramp + CHECKSUM_RESULT_OFFSET  # 0xa0200040
    sdbbp_pc = tramp + 0x30               # word 12 of 14-word program

    def file_xor_range(byte_off: int, byte_len: int) -> int:
        xor = 0
        for i in range(0, (byte_len // 4) * 4, 4):
            xor ^= int.from_bytes(file_data[byte_off + i:byte_off + i + 4], 'big')
        return xor & 0xFFFFFFFF

    # Write constant program words (indices 4-13) once; only words 0-3 change per chunk
    template_prog = make_checksum_program(load_addr, file_size)
    for i in range(4, len(template_prog)):
        ocd.cmd(f"mww 0x{tramp + i*4:08x} 0x{template_prog[i]:08x}")
    for i in range(4, len(template_prog)):
        rb = ocd.cmd(f"mdw 0x{tramp + i*4:08x}", timeout=5.0)
        m = re.search(r':\s+([0-9a-fA-F]{8})', rb)
        got = int(m.group(1), 16) if m else None
        if got != template_prog[i]:
            print(f"[-] cpu_scan: constant prog word[{i}] bad after write -- aborting")
            return False

    def run_chunk_xor(chunk_addr: int, chunk_size: int) -> int | None:
        """Run CPU XOR on [chunk_addr, chunk_addr+chunk_size). Returns XOR or None."""
        chunk_prog = make_checksum_program(chunk_addr, chunk_size)
        # Write + verify words 0-3 (start / end addresses)
        for i in range(4):
            ocd.cmd(f"mww 0x{tramp + i*4:08x} 0x{chunk_prog[i]:08x}")
        for i in range(4):
            rb = ocd.cmd(f"mdw 0x{tramp + i*4:08x}", timeout=5.0)
            m = re.search(r':\s+([0-9a-fA-F]{8})', rb)
            got = int(m.group(1), 16) if m else None
            if got != chunk_prog[i]:
                return None
        ocd.cmd(f"mww 0x{result_addr:08x} 0x00000000")   # sentinel
        ocd.cmd(f"resume {TRAMPOLINE_ADDR}", timeout=5.0)
        ocd.cmd("wait_halt 5000", timeout=10.0)
        time.sleep(0.1)
        ocd._drain(timeout=0.1)
        rpc = ocd.cmd("reg pc", timeout=5.0)
        m_pc = re.search(r'0x([0-9a-fA-F]+)', rpc)
        pc_val = int(m_pc.group(1), 16) if m_pc else None
        if pc_val != sdbbp_pc:
            ocd.cmd("halt", timeout=5.0)
            ocd.cmd("wait_halt 2000", timeout=5.0)
            ocd._drain(timeout=0.2)
            return None
        r = ocd.cmd(f"mdw 0x{result_addr:08x}", timeout=5.0)
        m = re.search(r':\s+([0-9a-fA-F]{8})', r)
        return int(m.group(1), 16) if m else None

    total_chunks = (file_size + CHUNK - 1) // CHUNK
    print(f"[*] CPU-XOR scanning {total_chunks} x 8KB chunks ...")
    bad_chunks = []

    for chunk_idx in range(total_chunks):
        off = chunk_idx * CHUNK
        chunk_size = min(CHUNK, file_size - off)
        chunk_size_aligned = (chunk_size // 4) * 4
        if chunk_size_aligned == 0:
            continue
        chunk_addr = load_addr + off
        expected = file_xor_range(off, chunk_size_aligned)

        actual = run_chunk_xor(chunk_addr, chunk_size_aligned)
        if actual is None:
            ocd.cmd("halt", timeout=5.0)
            ocd.cmd("wait_halt 2000", timeout=5.0)
            ocd._drain(timeout=0.2)
            actual = run_chunk_xor(chunk_addr, chunk_size_aligned)
        if actual != expected:
            bad_chunks.append((chunk_idx, off, chunk_size_aligned))

        if (chunk_idx + 1) % 100 == 0 or chunk_idx + 1 == total_chunks:
            print(f"  ... {chunk_idx+1}/{total_chunks} chunks scanned, {len(bad_chunks)} bad so far")

    if not bad_chunks:
        print("[+] CPU-XOR scan: all chunks clean [pass]")
        return True

    print(f"[!] {len(bad_chunks)} corrupt chunk(s) -- rewriting entirely from file ...")
    for chunk_idx, off, chunk_size_aligned in bad_chunks:
        chunk_addr = load_addr + off
        chunk_words = chunk_size_aligned // 4
        # Rewrite every word in the chunk (no guessing which words are bad)
        for w in range(chunk_words):
            word_val = int.from_bytes(file_data[off + w*4:off + w*4 + 4], 'big')
            ocd.cmd(f"mww 0x{chunk_addr + w*4:08x} 0x{word_val:08x}")
        # Verify with CPU XOR (up to 3 rewrite attempts)
        expected = file_xor_range(off, chunk_size_aligned)
        fixed = False
        for attempt in range(3):
            ocd.cmd("halt", timeout=5.0)
            ocd.cmd("wait_halt 2000", timeout=5.0)
            ocd._drain(timeout=0.2)
            actual = run_chunk_xor(chunk_addr, chunk_size_aligned)
            if actual == expected:
                print(f"  [+] Chunk {chunk_idx+1}/{total_chunks} (file+0x{off:x}) fixed [pass]")
                fixed = True
                break
            print(f"  [!] Chunk {chunk_idx+1} still bad (attempt {attempt+1}/3) -- rewriting again ...")
            for w in range(chunk_words):
                word_val = int.from_bytes(file_data[off + w*4:off + w*4 + 4], 'big')
                ocd.cmd(f"mww 0x{chunk_addr + w*4:08x} 0x{word_val:08x}")
        if not fixed:
            print(f"  [-] Chunk {chunk_idx+1} not fixable after 3 attempts -- aborting")
            return False

    print(f"[+] All {len(bad_chunks)} corrupt chunk(s) rewritten and CPU-XOR verified [pass]")
    return True


def load_and_run(ocd: OCD, _out: dict | None = None):
    with open(INITRAMFS, "rb") as f:
        file_data = f.read()
    sz = len(file_data)

    # Precompute the expected 32-bit XOR checksum of the binary
    expected_xor = compute_xor32(file_data)
    print(f"[*] Expected XOR32: 0x{expected_xor:08x}  ({sz // 1024} KB)")

    # -- Phase 0: D-cache flush BEFORE loading ------------------------------------
    # The Nandloader left dirty Cisco D-cache lines at physical 0x5FC00+.
    # We must evict them (with write-back) NOW, before load_image writes OpenWrt to
    # that same physical range.  If we flushed AFTER load_image the write-back would
    # overwrite our OpenWrt binary with stale Cisco data.
    print("[*] Flushing D-cache before load (cache 0x01 Index_WB_Invalidate, all 4 ways) ...")
    flush_sdbbp_pc = int(TRAMPOLINE_ADDR, 16) + 0x20  # sdbbp at word 8 of FLUSH_TRAMPOLINE
    for i, word in enumerate(FLUSH_TRAMPOLINE):
        addr = int(TRAMPOLINE_ADDR, 16) + i * 4
        ocd.cmd(f"mww 0x{addr:08x} 0x{word:08x}")
    flush_bad = 0
    for i, word in enumerate(FLUSH_TRAMPOLINE):
        addr = int(TRAMPOLINE_ADDR, 16) + i * 4
        rb = ocd.cmd(f"mdw 0x{addr:08x}", timeout=5.0)
        m = re.search(r':\s+([0-9a-fA-F]{8})', rb)
        got = int(m.group(1), 16) if m else None
        if got != word:
            got_s = f"0x{got:08x}" if got is not None else "None"
            print(f"[-] Flush trampoline word[{i}] bad: wrote 0x{word:08x} read {got_s}")
            flush_bad += 1
    if flush_bad:
        print(f"[-] Flush trampoline corrupt -- aborting")
        return False
    ocd.cmd(f"resume {TRAMPOLINE_ADDR}", timeout=5.0)
    ocd.cmd("wait_halt 2000", timeout=5.0)
    time.sleep(0.3)
    ocd._drain(timeout=0.3)
    rpc = ocd.cmd("reg pc", timeout=5.0)
    m_pc = re.search(r'0x([0-9a-fA-F]+)', rpc)
    pc_val = int(m_pc.group(1), 16) if m_pc else None
    if pc_val != flush_sdbbp_pc:
        pc_str = f"0x{pc_val:08x}" if pc_val is not None else "None"
        print(f"[-] D-cache flush SDBBP not hit -- PC={pc_str} expected 0x{flush_sdbbp_pc:08x}")
        print(f"    raw reg-pc response: {rpc!r}")
        ocd.cmd("halt", timeout=5.0)
        ocd.cmd("wait_halt 2000", timeout=5.0)
        ocd._drain(timeout=0.3)
        return False
    print("[+] D-cache pre-flush done [pass] -- dirty Cisco lines evicted before load")

    # -- Phase 1: load binary -----------------------------------------------------
    # At 1000 kHz PRACC writes ~97 KB/s; 6768 KB -> ~70s
    est_s = sz / 97000
    print(f"\n[*] Loading {sz // 1024} KB -> {LOAD_ADDR}  (est. {est_s:.0f}s at 1000 kHz) ...")
    t0 = time.monotonic()
    resp = ocd.cmd(f"load_image {INITRAMFS} {LOAD_ADDR} bin", timeout=180.0)
    elapsed = time.monotonic() - t0
    print(f"    load_image done in {elapsed:.1f}s")
    print(f"    {resp.strip()[:120]}")
    if "error" in resp.lower():
        print("[-] load_image failed -- aborting")
        return False

    # Re-assert halt (load_image can leave target in running state)
    print("[*] Re-halting CPU after load ...")
    r = ocd.cmd("halt", timeout=5.0)
    print(f"    {r.strip()[:80]}")
    ocd.cmd("wait_halt 2000", timeout=5.0)

    # -- Phase 2: second D-cache flush AFTER load --------------------------------
    # Belt-and-suspenders: repeat the full cache flush after load_image.
    # load_image uses KSEG1 (uncached) so it shouldn't introduce dirty D-cache
    # lines, but any speculative fetches or pipeline stalls during PRACC writes
    # that touch KSEG0 could leave residual lines.  This guarantees a clean slate
    # before the lzma-loader launches.
    print("[*] Post-load D-cache flush (cache 0x01 Index_WB_Invalidate, all 4 ways) ...")
    for i, word in enumerate(FLUSH_TRAMPOLINE):
        addr = int(TRAMPOLINE_ADDR, 16) + i * 4
        ocd.cmd(f"mww 0x{addr:08x} 0x{word:08x}")
    flush2_bad = 0
    for i, word in enumerate(FLUSH_TRAMPOLINE):
        addr = int(TRAMPOLINE_ADDR, 16) + i * 4
        rb = ocd.cmd(f"mdw 0x{addr:08x}", timeout=5.0)
        m = re.search(r':\s+([0-9a-fA-F]{8})', rb)
        got = int(m.group(1), 16) if m else None
        if got != word:
            got_s = f"0x{got:08x}" if got is not None else "None"
            print(f"[-] Post-load flush word[{i}] bad: wrote 0x{word:08x} read {got_s}")
            flush2_bad += 1
    if flush2_bad:
        print(f"[-] Post-load flush trampoline corrupt -- aborting")
        return False
    ocd.cmd(f"resume {TRAMPOLINE_ADDR}", timeout=5.0)
    ocd.cmd("wait_halt 2000", timeout=5.0)
    time.sleep(0.3)
    ocd._drain(timeout=0.3)
    rpc2 = ocd.cmd("reg pc", timeout=5.0)
    m_pc2 = re.search(r'0x([0-9a-fA-F]+)', rpc2)
    pc_val2 = int(m_pc2.group(1), 16) if m_pc2 else None
    if pc_val2 != flush_sdbbp_pc:
        pc_str2 = f"0x{pc_val2:08x}" if pc_val2 is not None else "None"
        print(f"[-] Post-load flush SDBBP not hit -- PC={pc_str2} expected 0x{flush_sdbbp_pc:08x}")
        ocd.cmd("halt", timeout=5.0)
        ocd.cmd("wait_halt 2000", timeout=5.0)
        ocd._drain(timeout=0.3)
        return False
    print("[+] Post-load D-cache flush done [pass]")

    # -- Phase 3: XOR checksum via CPU-executed MIPS program ----------------------
    # Much faster than PRACC-read-back (CPU runs at 560 MHz, not 3.5 KB/s PRACC).
    # Result stored at TRAMPOLINE_ADDR + CHECKSUM_RESULT_OFFSET.
    prog = make_checksum_program(int(LOAD_KSEG1, 16), sz)
    result_addr = int(TRAMPOLINE_ADDR, 16) + CHECKSUM_RESULT_OFFSET
    # SDBBP is instruction 12 (offset 0x30) in the checksum program
    xor_sdbbp_pc = int(TRAMPOLINE_ADDR, 16) + 0x30

    def run_xor(label="") -> int | None:
        """Write + run XOR program. Returns XOR result or None if SDBBP not hit."""
        # Sentinel: zero result slot so we can detect stale-read vs real result
        ocd.cmd(f"mww 0x{result_addr:08x} 0x00000000")
        for i, word in enumerate(prog):
            addr = int(TRAMPOLINE_ADDR, 16) + i * 4
            ocd.cmd(f"mww 0x{addr:08x} 0x{word:08x}")
        # Verify every XOR program word wrote correctly before running
        prog_bad = 0
        for i, word in enumerate(prog):
            addr = int(TRAMPOLINE_ADDR, 16) + i * 4
            rb = ocd.cmd(f"mdw 0x{addr:08x}", timeout=5.0)
            m_rb = re.search(r':\s+([0-9a-fA-F]{8})', rb)
            got_rb = int(m_rb.group(1), 16) if m_rb else None
            if got_rb != word:
                got_rb_str = f"0x{got_rb:08x}" if got_rb is not None else "None"
                print(f"  [!] {label}XOR prog word[{i}] bad: wrote 0x{word:08x} read {got_rb_str}")
                prog_bad += 1
        if prog_bad:
            print(f"  [!] {label}XOR program has {prog_bad} corrupt word(s) -- skipping run")
            return None
        ocd.cmd(f"resume {TRAMPOLINE_ADDR}", timeout=5.0)
        ocd.cmd("wait_halt 8000", timeout=15.0)  # ~25ms CPU time, generous timeout
        time.sleep(0.3)
        ocd._drain(timeout=0.3)
        # Verify PC landed at SDBBP (0xa0200030), not somewhere else
        rpc = ocd.cmd("reg pc", timeout=5.0)
        m_pc = re.search(r'0x([0-9a-fA-F]+)', rpc)
        pc_val = int(m_pc.group(1), 16) if m_pc else None
        pc_str = f"0x{pc_val:08x}" if pc_val is not None else "None"
        if pc_val != xor_sdbbp_pc:
            print(f"  [!] {label}XOR SDBBP not hit -- PC={pc_str} expected 0x{xor_sdbbp_pc:08x}")
            ocd.cmd("halt", timeout=5.0)
            ocd.cmd("wait_halt 2000", timeout=5.0)
            ocd._drain(timeout=0.3)
            return None
        r = ocd.cmd(f"mdw 0x{result_addr:08x}", timeout=5.0)
        m = re.search(r':\s+([0-9a-fA-F]{8})', r)
        return int(m.group(1), 16) if m else None

    print(f"[*] Running XOR checksum (14-word MIPS program, ~25ms CPU time) ...")
    got_xor = None
    for attempt in range(3):
        got_xor = run_xor(f"attempt {attempt+1}/3: ")
        if got_xor is not None:
            break
        print(f"  [!] Retrying XOR run ...")
        ocd.cmd("halt", timeout=5.0)
        ocd.cmd("wait_halt 2000", timeout=5.0)
        ocd._drain(timeout=0.3)

    if got_xor is None:
        print("[-] XOR checksum program failed to execute after 3 attempts -- aborting")
        return False

    if got_xor != expected_xor:
        print(f"[-] XOR mismatch: RAM=0x{got_xor:08x}  FILE=0x{expected_xor:08x}")
    else:
        print(f"[+] Full XOR match: 0x{got_xor:08x} [pass]  (XOR can miss cancelling errors -- running chunk scan)")

    # Always run cpu_scan_and_fix regardless of full XOR result.
    # A full-binary XOR can pass even with corrupt data if two bad words have the
    # same XOR delta (cancellation).  The chunk-level scan makes this astronomically
    # unlikely: each 8 KB chunk has ~0.07 expected errors, so two errors in the same
    # chunk is rare.  Only launch when EVERY chunk XOR AND the full XOR agree.
    print(f"[*] Re-halting to reset EJTAG state ...")
    ocd.cmd("halt", timeout=5.0)
    ocd.cmd("wait_halt 2000", timeout=5.0)
    ocd._drain(timeout=0.3)
    print(f"[*] Running cpu_scan_and_fix (CPU-XOR chunk scan, no bulk PRACC reads) ...")
    if not cpu_scan_and_fix(ocd, file_data, int(LOAD_KSEG1, 16)):
        print("[-] cpu_scan_and_fix failed -- aborting for retry")
        return False

    # Final full-binary XOR after chunk scan confirms everything is consistent
    print(f"[*] Final full XOR checksum after chunk scan ...")
    ocd.cmd("halt", timeout=5.0)
    ocd.cmd("wait_halt 2000", timeout=5.0)
    ocd._drain(timeout=0.3)
    got_xor2 = None
    for attempt in range(3):
        got_xor2 = run_xor(f"final attempt {attempt+1}/3: ")
        if got_xor2 is not None:
            break
        ocd.cmd("halt", timeout=5.0)
        ocd.cmd("wait_halt 2000", timeout=5.0)
        ocd._drain(timeout=0.3)

    got_xor2_str = f"0x{got_xor2:08x}" if got_xor2 is not None else "None"
    if got_xor2 != expected_xor:
        if got_xor2 is None:
            print(f"[-] Final XOR failed to run (3 attempts) -- aborting for retry")
        else:
            print(f"[-] Final XOR still wrong: RAM={got_xor2_str}  FILE=0x{expected_xor:08x}")
        print(f"[-] Binary not verified -- aborting for power-cycle retry")
        return False
    print(f"[+] Final XOR MATCH: {got_xor2_str} [pass]  -- chunk scan + full XOR both clean, launching!")

    # -- Phase 4: launch lzma-loader ---------------------------------------------
    print(f"[*] Writing launch trampoline (j {ENTRY_KSEG1}) -> {TRAMPOLINE_ADDR} ...")
    for i, word in enumerate(LAUNCH_TRAMPOLINE):
        addr = int(TRAMPOLINE_ADDR, 16) + i * 4
        ocd.cmd(f"mww 0x{addr:08x} 0x{word:08x}")
    r = ocd.cmd(f"mdw {TRAMPOLINE_ADDR}", timeout=5.0)
    m = re.search(r':\s+([0-9a-fA-F]{8})', r)
    got = int(m.group(1), 16) if m else None
    if got != LAUNCH_TRAMPOLINE[0]:
        got_str = f"0x{got:08x}" if got is not None else "None"
        print(f"[-] Launch trampoline word[0] bad: {got_str}")
        return False
    print("[+] Launch trampoline written")

    # -- Phase 4b: launch lzma-loader + start UART failsafe thread ---------------
    # Previous approach (runs 4--5): set HW BP at kernel entry (0x80060000), halt,
    # sanitize a0-a3 registers, then set a second HW BP at proc_dostring and resume.
    # Root cause of regression: the combined PRACC activity (8 reg r/w while halted
    # at a HW BP, then rbp+resume on a running CPU) corrupted EJTAG state and the
    # kernel produced zero UART output on runs 4 and 5.
    # Run 3 (no Phase 4b/4c) showed full UART output -- the kernel boots fine with
    # lzma-loader register residue; the kernel's exception handler catches the TLBL
    # from the stale a1 pointer (0x1000ffc4) gracefully.
    # Fix: resume directly from the trampoline, start the UART thread immediately,
    # and let the kernel boot without any further EJTAG intrusion.
    print(f"[*] Resuming at {TRAMPOLINE_ADDR} -> lzma-loader at {ENTRY_KSEG1} ...")
    resp = ocd.cmd(f"resume {TRAMPOLINE_ADDR}", timeout=5.0)
    print(f"    {resp.strip()[:80]}")

    print()
    print("=" * 60)
    print("[+] Initramfs kernel launched! Waiting for OpenWrt to boot ...")
    print("=" * 60)

    # -- Start combined UART reader + failsafe trigger immediately ----------------
    # Watches for OpenWrt preinit "Press the [f] key" prompt and sends 'f\n' to
    # trigger failsafe mode.  EN/GPIO17 via RTS is also toggled (belt-and-suspenders
    # -- only UART 'f' is actually needed, but keeping EN for future-proofing).
    t_kernel_start = time.monotonic()
    _uart_en_stop = threading.Event()
    _failsafe_active = threading.Event()   # set once failsafe shell is confirmed

    def _uart_en_fn():
        buf = b""
        en_asserted = False
        en_released = False
        failsafe_sent = False
        shell_setup_done = False
        try:
            with serial.Serial(ESPPROG_UART, 115200, timeout=0.1) as ser:
                ser.rts = False
                while not _uart_en_stop.is_set():
                    elapsed = time.monotonic() - t_kernel_start
                    if not en_asserted and elapsed >= FAILSAFE_EN_DELAY:
                        ser.rts = True
                        en_asserted = True
                        print(f"[+] EN asserted at t={elapsed:.1f}s -- "
                              f"GPIO17 LOW via NPN (holding {FAILSAFE_EN_HOLD:.0f}s) ...",
                              flush=True)
                    if en_asserted and not en_released and \
                            elapsed >= FAILSAFE_EN_DELAY + FAILSAFE_EN_HOLD:
                        ser.rts = False
                        en_released = True
                        print(f"[+] EN released at t={elapsed:.1f}s", flush=True)
                    chunk = ser.read(ser.in_waiting or 1)
                    if chunk:
                        buf += chunk
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            t = time.monotonic() - t_kernel_start
                            text = line.decode("utf-8", errors="replace").rstrip("\r")
                            print(f"  [uart t={t:5.1f}s] {text}", flush=True)
                            # Detect OpenWrt preinit failsafe prompt and respond via UART
                            if not failsafe_sent and (
                                "failsafe mode" in text.lower() or
                                "press the [f] key" in text.lower()
                            ):
                                ser.write(b"f\n")
                                ser.flush()
                                failsafe_sent = True
                                print(f"  [+] Sent 'f\\n' to UART at t={t:.1f}s"
                                      f" -- triggering failsafe!", flush=True)
                            # Once failsafe shell is active, kick watchdog + bring up network.
                            # Root cause: QCA9558 HW watchdog fires at ~90s if not fed;
                            # initramfs failsafe mode may not start procd's watchdog feeder,
                            # and eth0 may have no IP assigned.  Fix: send shell commands
                            # to disable/feed the watchdog, configure eth0, start telnetd.
                            if failsafe_sent and not shell_setup_done and (
                                "openwrt-failsafe" in text.lower() or
                                "failsafe" in text.lower() and "/#" in text
                            ):
                                time.sleep(0.5)   # let ash settle
                                cmds = [
                                    # Feed/disable the HW watchdog so the device doesn't
                                    # reset at ~90s before sysupgrade completes.
                                    b"( while true; do echo 1 > /dev/watchdog; sleep 5; done ) &\n",
                                    # Bring up eth0 with the static IP expected by do_sysupgrade_telnet.
                                    b"ifconfig eth0 192.168.1.1 netmask 255.255.255.0 up\n",
                                    # Start telnetd (no password in failsafe) if not already running.
                                    b"telnetd -l /bin/sh &\n",
                                ]
                                for cmd in cmds:
                                    ser.write(cmd)
                                    ser.flush()
                                    time.sleep(0.3)
                                shell_setup_done = True
                                _failsafe_active.set()   # prevents PSU shutdown on timeout
                                print(f"  [+] Failsafe shell setup sent at t={t:.1f}s"
                                      f" (watchdog kicker + ifconfig + telnetd)", flush=True)
                    else:
                        time.sleep(0.01)
        except Exception as e:
            print(f"  [uart/en] thread exited: {e}", flush=True)

    _uart_en_thread = threading.Thread(target=_uart_en_fn, daemon=True)
    _uart_en_thread.start()
    if _out is not None:
        _out['uart_thread']      = _uart_en_thread
        _out['uart_stop']        = _uart_en_stop
        _out['failsafe_active']  = _failsafe_active
    return True


# -- post-boot: failsafe trigger + NIC config + sysupgrade --------------------

def trigger_failsafe_gpio(ocd: OCD):
    """Simulate reset button press (GPIO17 active-LOW) during OpenWrt preinit.

    Root cause: the initramfs preinit mounts the Meraki NAND overlay, so the
    Meraki management daemons run instead of OpenWrt's networking.  The device
    acquires an IP via DHCP (as WAN) rather than coming up at 192.168.1.1.

    Fix: trigger OpenWrt failsafe mode by pulling GPIO17 LOW while preinit is
    polling the reset button.  Failsafe skips the NAND overlay mount entirely,
    bringing the device up with a static 192.168.1.1 and telnetd on port 23.

    How: hammer GPIO17 LOW via JTAG PRACC writes every 1.5s for 25 seconds,
    starting IMMEDIATELY after kernel launch.  This fights the gpio-keys driver
    which sets GPIO17 as input during kernel init (~t=5-8s) -- we re-override
    the direction register every cycle so the pin stays physically LOW during
    the preinit failsafe check window (~t=10-18s).
    """
    print(f"[*] Hammering GPIO17 LOW for {FAILSAFE_HAMMER_DURATION}s "
          f"(every {FAILSAFE_HAMMER_INTERVAL}s) to cover preinit failsafe window ...")
    # (?:0x)? handles both "0xdeadbeef" and "deadbeef" OpenOCD value formats
    _mdw_re = re.compile(r':\s+(?:0x)?([0-9a-fA-F]{8})')

    def mdw_val(addr) -> int | None:
        r = ocd.cmd(f"mdw 0x{addr:08x}", timeout=5.0)
        m = _mdw_re.search(r)
        return int(m.group(1), 16) if m else None

    t_end = time.monotonic() + FAILSAFE_HAMMER_DURATION
    cycle = 0
    while time.monotonic() < t_end:
        elapsed = FAILSAFE_HAMMER_DURATION - max(0.0, t_end - time.monotonic())
        # CRITICAL: OpenOCD MIPS EJTAG returns ERROR_TARGET_NOT_HALTED for mdw/mww
        # on a running target -- they silently fail.  Must explicitly halt first.
        ocd.cmd("halt", timeout=5.0)
        ocd.cmd("wait_halt 2000", timeout=5.0)
        time.sleep(0.1)
        ocd._drain(timeout=0.2)

        # --- Diagnostic snapshot BEFORE writes ---
        oe_before  = mdw_val(GPIO_OE)
        in_before  = mdw_val(GPIO_IN)
        out_before = mdw_val(GPIO_OUT)
        func_val   = mdw_val(GPIO_FUNC)

        # --- Drive GPIO17 LOW ---
        # Hypothesis: GPIO17 on QCA9557 is open-drain with inverted convention:
        #   OUT=0 (CLR) -> transistor OFF -> pin released -> pull-up wins -> HIGH
        #   OUT=1 (SET) -> transistor ON  -> pin pulled to GND -> LOW
        # Meraki had OE=1, OUT=0, IN=1 -- consistent with "released" open-drain.
        # Try GPIO_SET (not GPIO_CLR) to assert LOW.
        old_oe = oe_before if oe_before is not None else 0
        new_oe = old_oe | RESET_GPIO_BIT
        ocd.cmd(f"mww 0x{GPIO_OE:08x} 0x{new_oe:08x}")           # GPIO17 -> output
        ocd.cmd(f"mww 0x{GPIO_SET:08x} 0x{RESET_GPIO_BIT:08x}")  # open-drain: SET -> GND
        ocd.cmd(f"mww 0x{GPIO_CLR:08x} 0x{RESET_GPIO_BIT:08x}")  # push-pull:  CLR -> LOW
        # Direct write to GPIO_OUT: try both 0 and 1 -- OUT=1 in open-drain = pull to GND
        out_hi = (out_before | RESET_GPIO_BIT) if out_before is not None else RESET_GPIO_BIT
        ocd.cmd(f"mww 0x{GPIO_OUT:08x} 0x{out_hi:08x}")

        # --- Diagnostic snapshot AFTER writes ---
        oe_after  = mdw_val(GPIO_OE)
        in_after  = mdw_val(GPIO_IN)
        out_after = mdw_val(GPIO_OUT)

        # Resume so kernel can run during the sleep interval
        ocd.cmd("resume", timeout=3.0)
        cycle += 1

        def b17(v): return f"{(v>>17)&1}" if v is not None else '?'
        print(f"  [t={elapsed:4.1f}s c{cycle}] "
              f"OE={b17(oe_before)}->{b17(oe_after)} "
              f"OUT={b17(out_before)}->{b17(out_after)} "
              f"IN={b17(in_before)}->{b17(in_after)} "
              f"FUNC={f'0x{func_val:08x}' if func_val is not None else '?'}")
        time.sleep(FAILSAFE_HAMMER_INTERVAL)
    print(f"[+] GPIO17 hammer done ({cycle} cycles over {FAILSAFE_HAMMER_DURATION}s) "
          f"-- failsafe window fully covered")


def do_sysupgrade_telnet() -> bool:
    """Transfer sysupgrade image and flash via telnet (OpenWrt failsafe, port 23).

    OpenWrt failsafe mode starts telnetd on port 23 with no password.
    Steps:
      1. telnet to 192.168.1.1:23, get root shell
      2. Start nc listener on MR18 (nc -l -p 9000 > /tmp/sysupgrade.bin)
      3. From host, pipe sysupgrade.bin to nc 192.168.1.1 9000
      4. Run sysupgrade -n /tmp/sysupgrade.bin via telnet
    """
    if not os.path.exists(SYSUPGRADE):
        print(f"[-] Sysupgrade image not found: {SYSUPGRADE}")
        return False

    sz = os.path.getsize(SYSUPGRADE)

    print(f"[*] Connecting to {OPENWRT_IP}:23 (telnet, failsafe mode) ...")
    try:
        sock = socket.socket()
        sock.settimeout(10.0)
        sock.connect((OPENWRT_IP, 23))
    except Exception as e:
        print(f"[-] Telnet connect failed: {e}")
        return False

    def recv_until(prompt=b"# ", timeout=15.0) -> str:
        """Receive bytes, strip IAC negotiations, return decoded text."""
        data = b""
        sock.settimeout(0.5)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                # Handle telnet IAC (0xFF) option negotiations
                i = 0
                while i < len(chunk):
                    b = chunk[i]
                    if b == 0xFF and i + 2 < len(chunk):
                        cmd_b, opt_b = chunk[i + 1], chunk[i + 2]
                        if cmd_b in (0xFD, 0xFB):        # DO / WILL
                            neg = 0xFC if cmd_b == 0xFD else 0xFE   # WONT / DONT
                            sock.send(bytes([0xFF, neg, opt_b]))
                        i += 3
                    else:
                        data += bytes([b])
                        i += 1
                if prompt in data[-30:]:
                    break
            except socket.timeout:
                pass
        return data.decode(errors="replace")

    def tsh(cmd: str, timeout=10.0) -> str:
        """Send a shell command, wait for prompt, return output."""
        sock.send((cmd + "\n").encode())
        return recv_until(timeout=timeout)

    # Wait for initial shell prompt
    output = recv_until(timeout=30)
    if "# " not in output:
        sock.send(b"\n")
        output = recv_until(timeout=10)
    print(f"    telnet: {output.strip()[-80:]!r}")
    if "# " not in output:
        print("[-] No shell prompt from telnet")
        sock.close()
        return False

    # Start nc listener on the MR18 in the background
    print(f"[*] Starting nc listener on MR18 port 9000 ...")
    tsh("nc -l -p 9000 > /tmp/sysupgrade.bin &")
    time.sleep(1)

    # Send the sysupgrade image from host to MR18 via nc
    print(f"[*] Sending sysupgrade.bin ({sz // 1024} KB) to MR18 via nc ...")
    with open(SYSUPGRADE, "rb") as f:
        nc_r = subprocess.run(
            ["nc", "-w", "120", OPENWRT_IP, "9000"],
            stdin=f, timeout=300,
        )
    if nc_r.returncode not in (0, 1):   # nc exits 1 when remote closes; that's ok
        print(f"[-] nc returned {nc_r.returncode}")
        sock.close()
        return False
    print(f"    nc done (rc={nc_r.returncode})")

    # Allow background nc on MR18 to finish
    time.sleep(3)

    # Verify file size on MR18
    size_out = tsh("wc -c /tmp/sysupgrade.bin")
    print(f"    remote size: {size_out.strip()}")

    # Flash
    print("[*] Running sysupgrade -n on MR18 (device will reboot) ...")
    sock.send(b"sysupgrade -n /tmp/sysupgrade.bin\n")
    print("[+] sysupgrade launched -- MR18 is writing OpenWrt to NAND and will reboot")
    time.sleep(5)
    sock.close()
    return True


def trigger_failsafe_en():
    """Simulate MR18 reset button press via ESP-Prog EN pin.

    The JTAG GPIO hammer (trigger_failsafe_gpio) was defeated by the MR18 reset
    supervisor IC, which drives GPIO17 HIGH with a CMOS push-pull output strong
    enough to overwhelm the SoC's GPIO driver (Bug 20).

    The ESP-Prog UART connector exposes an EN pin that is controlled by the
    FT2232H's RTS line through an NPN transistor on the ESP-Prog board.  Setting
    ser.rts = True drives the transistor base HIGH, the collector pulls low, and
    the EN pin is pulled to GND.  If this pin is wired to the non-GND pad of the
    MR18 reset button (the GPIO17 net), it acts as a software-controlled button
    press -- without any JTAG involvement.

    Timing: the initramfs binary is 6.9MB compressed -> ~26MB decompressed kernel.
    At ~2 MB/s LZMA output on 560MHz MIPS, decompression alone takes ~13s, then
    kernel init adds ~5-8s before preinit starts.  We assert from FAILSAFE_EN_DELAY
    (2s) and hold for FAILSAFE_EN_HOLD (40s) to blanket the entire preinit window.

    UART output from MR18 is printed to stdout in parallel for timing diagnostics.
    """
    print(f"[*] Waiting {FAILSAFE_EN_DELAY:.0f}s before asserting EN "
          f"(LZMA decompression window) ...")
    try:
        with serial.Serial(ESPPROG_UART, 115200, timeout=0.1) as ser:
            time.sleep(FAILSAFE_EN_DELAY)

            # RTS=True -> NPN transistor ON -> EN pin pulled LOW -> GPIO17 LOW
            ser.rts = True
            print(f"[+] EN asserted -- GPIO17 pulled LOW via ESP-Prog NPN, "
                  f"holding {FAILSAFE_EN_HOLD:.0f}s (covering full preinit window) ...")
            time.sleep(FAILSAFE_EN_HOLD)
            ser.rts = False
            print("[+] EN released -- GPIO17 back to HIGH")

    except Exception as e:
        print(f"[-] EN trigger failed: {e}")
        print(f"    Check ESPPROG_UART={ESPPROG_UART} is the ESP-Prog UART interface")
        print("    and that the EN pin is wired to the MR18 reset button non-GND pad.")
        print("    MANUAL FALLBACK: press the reset button on the MR18 for 1 second NOW.")


def configure_nic():
    """Bring up host NIC with static IP for direct MR18 link."""
    subprocess.run(["ip", "addr", "flush", "dev", HOST_NIC], capture_output=True)
    subprocess.run(["ip", "addr", "add", HOST_IP, "dev", HOST_NIC], capture_output=True)
    subprocess.run(["ip", "link", "set", HOST_NIC, "up"], capture_output=True)
    print(f"[*] NIC {HOST_NIC} configured as {HOST_IP}")


def wait_for_openwrt(timeout=720) -> bool:
    """Poll 192.168.1.1 until it responds: try ARP (nmap -sn), ICMP ping, and TCP/23.

    ICMP may be filtered; ARP (L2) and TCP/23 (telnetd in failsafe) are more reliable.
    Also tries TCP/80 (LuCI web UI in normal boot).
    """
    print(f"[*] Waiting for OpenWrt at {OPENWRT_IP} (up to {timeout}s) ...")
    deadline = time.monotonic() + timeout
    last_status = ""
    while time.monotonic() < deadline:
        # ARP-level detection (works even if ICMP is firewalled)
        r_arp = subprocess.run(
            ["nmap", "-sn", OPENWRT_IP],
            capture_output=True, text=True,
        )
        arp_up = "Host is up" in r_arp.stdout

        # ICMP ping
        r_ping = subprocess.run(
            ["ping", "-c", "1", "-W", "1", OPENWRT_IP],
            capture_output=True,
        )
        icmp_up = r_ping.returncode == 0

        # TCP port probe -- telnet/23 (failsafe) or http/80 (normal boot)
        tcp_up = False
        tcp_port = 0
        for port in (23, 80):
            try:
                s = socket.socket()
                s.settimeout(1.0)
                rc = s.connect_ex((OPENWRT_IP, port))
                s.close()
                if rc == 0:
                    tcp_up = True
                    tcp_port = port
                    break
            except Exception:
                pass

        status = f"ARP={'up' if arp_up else 'no'} ICMP={'up' if icmp_up else 'no'} TCP={tcp_port if tcp_up else 'no'}"
        if status != last_status:
            print(f"    {status}")
            last_status = status

        if arp_up or icmp_up or tcp_up:
            print(f"[+] {OPENWRT_IP} detected! ({status})")
            return True
        time.sleep(2)
    return False


def do_sysupgrade() -> bool:
    """SCP sysupgrade image to MR18 and run sysupgrade -n via SSH."""
    if not os.path.exists(SYSUPGRADE):
        print(f"[-] Sysupgrade image not found: {SYSUPGRADE}")
        return False

    ssh_opts = [
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=10",
    ]
    target = f"root@{OPENWRT_IP}"

    print(f"[*] Copying sysupgrade image to {OPENWRT_IP}:/tmp/sysupgrade.bin ...")
    r = subprocess.run(
        ["scp"] + ssh_opts + [SYSUPGRADE, f"{target}:/tmp/sysupgrade.bin"],
        timeout=120,
    )
    if r.returncode != 0:
        print(f"[-] scp failed (rc={r.returncode}) -- trying telnet fallback ...")
        return do_sysupgrade_telnet()

    print("[*] Running sysupgrade -n (no config preserve) ...")
    # sysupgrade reboots the device -- SSH will disconnect mid-command, that's expected
    subprocess.run(
        ["ssh"] + ssh_opts + [target, "sysupgrade -n /tmp/sysupgrade.bin"],
        timeout=30,
    )
    print("[+] sysupgrade launched -- device is flashing NAND and will reboot")
    return True


# -- main ---------------------------------------------------------------------

MAX_ATTEMPTS = 6

def main():
    if not os.path.exists(INITRAMFS):
        print(f"[-] Initramfs not found: {INITRAMFS}")
        sys.exit(1)

    # Kill any stale processes before starting fresh
    kill_openocd()
    kill_repl()

    tail_proc, repl_proc = start_repl()

    try:
        # Set PSU voltage and current limit BEFORE any output is enabled.
        # 12V @ 1.5A: the 1.5A is the hardware current limit -- protects the MR18.
        psu("psu set 12 1.5", delay=0.5)

        halted = False
        proc = None
        ocd = None
        t0 = 0.0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            print(f"\n-- Attempt {attempt}/{MAX_ATTEMPTS} ----------------------------")

            # Power off and kill any stale OpenOCD
            print("[*] Powering off MR18 ...")
            psu("psu chan off")
            if proc is not None:
                proc.terminate()
                proc = None
            if ocd is not None:
                ocd.close()
                ocd = None
            kill_openocd()
            time.sleep(2.5)

            # Power on -- Nandloader starts running
            print("[*] Powering on MR18 -- waiting for Nandloader ...")
            psu("psu chan on", delay=0.1)

            # Wait for Nandloader to be active (~1.5s sweet spot for AR9344)
            time.sleep(1.5)

            # Start OpenOCD NOW with -c init so it scans a LIVE TAP
            print("[*] Starting OpenOCD (live TAP scan) ...")
            proc = start_openocd()

            ocd = OCD()
            print("[*] Waiting for OpenOCD telnet ...")
            if not ocd.connect(retries=15):
                print("    [-] Could not connect to OpenOCD -- check /tmp/openocd.log")
                continue
            print("[+] OpenOCD telnet connected")

            # Tight halt loop -- TAP already examined by -c init, just hammer halt
            t0 = time.monotonic()
            while time.monotonic() - t0 < 1.0:
                if try_halt_once(ocd):
                    halted = True
                    break
                if try_halt_ejtag(ocd):
                    halted = True
                    break
                time.sleep(0.02)

            if halted:
                print(f"\n[+] CPU HALTED in {time.monotonic() - t0:.2f}s!")
                break

            print(f"[-] Attempt {attempt} failed (elapsed {time.monotonic() - t0:.2f}s)")

        if not halted:
            print("\n[-] Failed to halt CPU after all attempts.")
            print("    Check /tmp/openocd.log for details.")
            if ocd:
                ocd.close()
            if proc:
                proc.terminate()
            sys.exit(1)

        # Load image and jump.  The UART+EN thread is started inside load_and_run()
        # immediately after the trampoline resume.  Thread references are returned
        # via the _out dict.
        _lout: dict = {}
        if not load_and_run(ocd, _lout):
            ocd.close()
            proc.terminate()
            sys.exit(1)
        _uart_thread    = _lout.get('uart_thread')
        _uart_stop      = _lout.get('uart_stop')
        _failsafe_event = _lout.get('failsafe_active')

        configure_nic()  # configure host NIC 192.168.1.2/24

        # Close JTAG -- kernel is running, UART+EN thread is already active in background.
        ocd.close()
        proc.terminate()
        ocd = None

        if not wait_for_openwrt(timeout=300):
            # If the UART thread confirmed the failsafe shell is up (ifconfig + telnetd
            # already sent), the device IS running OpenWrt -- do NOT power-cycle via
            # sys.exit.  Give it a longer window in case telnetd just needs more time.
            if _failsafe_event and _failsafe_event.is_set():
                print("[!] wait_for_openwrt timed out but failsafe shell was confirmed --"
                      " waiting an extra 120s before giving up (PSU stays ON) ...")
                if not wait_for_openwrt(timeout=120):
                    print("[-] Still no response at 192.168.1.1 after extended wait.")
                    print(f"    Flash manually: browse to http://{OPENWRT_IP} and upload:")
                    print(f"    {SYSUPGRADE}")
                    sys.exit(1)
            else:
                if _uart_stop:
                    _uart_stop.set()
                if _uart_thread:
                    _uart_thread.join(timeout=3.0)
                print("[-] Timed out waiting for OpenWrt.")
                print(f"    Flash manually: browse to http://{OPENWRT_IP} and upload:")
                print(f"    {SYSUPGRADE}")
                sys.exit(1)

        if _uart_stop:
            _uart_stop.set()
        if _uart_thread:
            _uart_thread.join(timeout=3.0)

        if do_sysupgrade():
            print()
            print("=" * 60)
            print("[+] ALL DONE -- OpenWrt is being written to NAND.")
            print("    Wait ~90s for the device to reboot, then connect to")
            print(f"    http://{OPENWRT_IP} to confirm.")
            print("=" * 60)
        else:
            print(f"[-] Automated sysupgrade failed.")
            print(f"    Flash manually: browse to http://{OPENWRT_IP} and upload:")
            print(f"    {SYSUPGRADE}")

    finally:
        # Always kill the repl on exit -- this triggers the safe state:
        # PSU output disabled, voltage set to 0V, current limit set to 0A.
        repl_proc.terminate()
        tail_proc.terminate()
        repl_proc.wait(timeout=5)
        tail_proc.wait(timeout=5)


if __name__ == "__main__":
    main()
