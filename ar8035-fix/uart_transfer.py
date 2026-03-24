#!/usr/bin/env python3
"""
UART hex transfer v3.
Fixes v2 by using CHUNK_SIZE=512 (1024-char hex lines, safe below busybox awk limits).
Also runs a pre-test with 32 known bytes before committing to the full 20-min transfer.
"""
import serial, time, sys, os, binascii, threading, hashlib

_SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
SYSUPGRADE   = os.path.join(_PROJECT_DIR, "firmware", "openwrt-25.12.0-ath79-nand-meraki_mr18-squashfs-sysupgrade.bin")
UART         = "/dev/ttyUSB4"
BAUD         = 115200
EXPECTED_MD5 = "53e272bed2041616068c6958fe28a197"
CHUNK_SIZE   = 512   # bytes per hex line -> 1024 hex chars; safe below any busybox awk limit

# The awk hex decoder.  One line of hex chars in -> binary bytes out.
# index() is 1-based; "not found" returns 0, making value negative -> wrong byte.
# This is fine as long as the stream is clean hex (0-9, a-f).
AWK_CMD = (
    b"awk 'BEGIN{h=\"0123456789abcdef\"}"
    b"{for(i=1;i<=length($0);i+=2)"
    b"printf\"%c\",(index(h,tolower(substr($0,i,1)))-1)*16+"
    b"(index(h,tolower(substr($0,i+1,1)))-1)}'"
    b" > /tmp/fw.bin 2>/dev/null\n"
)

AWK_TEST_CMD = (
    b"awk 'BEGIN{h=\"0123456789abcdef\"}"
    b"{for(i=1;i<=length($0);i+=2)"
    b"printf\"%c\",(index(h,tolower(substr($0,i,1)))-1)*16+"
    b"(index(h,tolower(substr($0,i+1,1)))-1)}'"
    b" > /tmp/awk_test.bin 2>/dev/null\n"
)


def drain(ser, secs=1.0):
    deadline = time.monotonic() + secs
    buf = b''
    while time.monotonic() < deadline:
        n = ser.in_waiting
        if n:
            buf += ser.read(n)
        else:
            time.sleep(0.02)
    return buf


def send_line(ser, line_bytes):
    """Send a bytes object followed by \\n and flush."""
    ser.write(line_bytes + b'\n')
    ser.flush()


def wait_prompt(ser, timeout=30.0):
    """Wait for shell # prompt."""
    buf = b''
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        n = ser.in_waiting
        if n:
            chunk = ser.read(n)
            buf += chunk
            if b'#' in chunk:
                time.sleep(0.3)
                buf += ser.read(ser.in_waiting or 0)
                return buf.decode('utf-8', errors='replace')
        else:
            time.sleep(0.05)
    return buf.decode('utf-8', errors='replace')


def send_cmd(ser, cmd_bytes, timeout=15.0):
    ser.write(cmd_bytes + b'\n')
    ser.flush()
    return wait_prompt(ser, timeout=timeout)


# ------------------------------------------------------------------------------
# Phase 0: known-data pre-test (32 bytes)
# ------------------------------------------------------------------------------
TEST_BYTES = bytes(range(32))   # 0x00 0x01 ... 0x1f
TEST_HEX   = binascii.hexlify(TEST_BYTES)          # b'000102...1f'
TEST_MD5   = hashlib.md5(TEST_BYTES).hexdigest()    # known answer


def run_pretest(ser):
    print("[*] Pre-test: sending 32 known bytes through awk decoder ...")
    # Clear any old test file
    send_cmd(ser, b"rm -f /tmp/awk_test.bin", timeout=5)

    # Start awk reading from stdin -> /tmp/awk_test.bin
    ser.write(AWK_TEST_CMD)
    ser.flush()
    time.sleep(1.0)
    drain(ser, 0.5)

    # Send the 32-byte hex string + EOF
    ser.write(TEST_HEX + b'\n')
    ser.flush()
    time.sleep(0.5)
    ser.write(b'\x04')   # EOF
    ser.flush()
    time.sleep(1.5)
    drain(ser, 0.5)

    # Verify size
    r = send_cmd(ser, b"wc -c /tmp/awk_test.bin", timeout=10)
    print(f"  wc -c: {r.strip()}")
    if '32' not in r:
        print(f"[!] Pre-test FAIL: expected 32 bytes, got: {r.strip()}")
        return False

    # Verify md5
    r = send_cmd(ser, b"md5sum /tmp/awk_test.bin", timeout=10)
    print(f"  md5sum: {r.strip()}")
    if TEST_MD5 in r:
        print(f"[+] Pre-test PASS: md5 {TEST_MD5} [pass]")
        return True
    else:
        print(f"[-] Pre-test FAIL: expected {TEST_MD5}")
        return False


# ------------------------------------------------------------------------------
# Phase 1: full transfer
# ------------------------------------------------------------------------------
def run_transfer(ser):
    with open(SYSUPGRADE, 'rb') as f:
        data = f.read()
    filesize = len(data)
    print(f"[*] Transferring {filesize:,} bytes, chunk={CHUNK_SIZE}B ({CHUNK_SIZE*2} hex chars/line) ...")

    # Clean up any previous attempt
    send_cmd(ser, b"rm -f /tmp/fw.bin", timeout=10)

    # Start awk
    ser.write(AWK_CMD)
    ser.flush()
    time.sleep(1.0)
    drain(ser, 0.5)

    # Start echo drainer
    stop_drain = threading.Event()
    drain_bytes = [0]
    def echo_drainer():
        while not stop_drain.is_set():
            n = ser.in_waiting
            if n:
                drain_bytes[0] += len(ser.read(n))
            else:
                time.sleep(0.005)
    drainer = threading.Thread(target=echo_drainer, daemon=True)
    drainer.start()

    t0 = time.monotonic()
    total_sent = 0

    for offset in range(0, filesize, CHUNK_SIZE):
        chunk = data[offset:offset + CHUNK_SIZE]
        hexline = binascii.hexlify(chunk) + b'\n'
        ser.write(hexline)
        total_sent += len(chunk)

        if (offset // CHUNK_SIZE) % 500 == 0 or offset + CHUNK_SIZE >= filesize:
            elapsed = time.monotonic() - t0
            rate = total_sent / elapsed / 1024 if elapsed > 0 else 0
            eta  = (filesize - total_sent) / (total_sent / elapsed) if total_sent > 0 and elapsed > 0 else 0
            pct  = total_sent * 100 // filesize
            print(f"  {pct:3d}%  {total_sent//1024}KB/{filesize//1024}KB  "
                  f"{rate:.1f}KB/s  ETA {eta:.0f}s  (echo drained {drain_bytes[0]//1024}KB)", flush=True)

    ser.flush()
    elapsed = time.monotonic() - t0
    print(f"[+] Hex sent: {total_sent:,} bytes in {elapsed:.1f}s")

    # Send EOF; stop drainer
    time.sleep(1.0)
    stop_drain.set()
    drainer.join(timeout=3.0)
    print(f"[*] Echo drained: {drain_bytes[0]:,} bytes total")

    print("[*] Sending EOF to terminate awk ...")
    ser.write(b'\x04')
    ser.flush()

    print("[*] Waiting for awk to finish writing ...")
    time.sleep(3.0)
    wait_prompt(ser, timeout=90.0)
    drain(ser, 1.0)

    # Verify
    print("[*] Checking file size ...")
    r = send_cmd(ser, b"wc -c /tmp/fw.bin", timeout=15)
    print(f"  {r.strip()}")

    print("[*] Verifying md5sum ...")
    r = send_cmd(ser, b"md5sum /tmp/fw.bin", timeout=60)
    print(f"  {r.strip()}")

    if EXPECTED_MD5 in r:
        print("[+] MD5 MATCH [pass] -- running sysupgrade ...")
        ser.write(b"sysupgrade /tmp/fw.bin\n")
        ser.flush()
        sysup = wait_prompt(ser, timeout=300.0)
        print(f"  {sysup[:800]}")
        return True
    else:
        print("[-] MD5 MISMATCH.")
        return False


# ------------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------------
with serial.Serial(UART, BAUD, timeout=0.5) as ser:
    ser.rts = False
    time.sleep(0.3)
    drain(ser, 0.5)

    # Poke the shell -- make sure MR18 is alive
    print("[*] Poking MR18 shell ...")
    r = send_cmd(ser, b"echo alive", timeout=10)
    if 'alive' not in r:
        print(f"[!] Shell not responding: {r!r}")
        print("    MR18 may have rebooted -- re-run mr18_flash.py first.")
        sys.exit(1)
    print(f"[+] Shell alive: {r.strip()!r}")

    # Kill any leftover background jobs
    send_cmd(ser, b"kill $(jobs -p) 2>/dev/null; true", timeout=5)
    drain(ser, 0.5)

    # Pre-test
    ok = run_pretest(ser)
    if not ok:
        print("\n[!] Pre-test failed -- awk formula or TTY is broken.")
        print("    Investigate before attempting full transfer.")
        sys.exit(1)

    print()
    print("[*] Pre-test passed -- proceeding with full transfer.")
    print(f"[*] ETA ~{(len(open(SYSUPGRADE,'rb').read())*2/11520):.0f}s at 115200 baud")
    run_transfer(ser)
