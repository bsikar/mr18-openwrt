#!/usr/bin/env python3
"""
Transfer ar8035_min2 to MR18, run it, show output.
Uses same hex-encode approach as uart_transfer_v3.py.
"""
import serial, time, sys, os, binascii, hashlib

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BINARY   = os.path.join(_SCRIPT_DIR, "ar8035-fix")
UART     = "/dev/ttyUSB4"
BAUD     = 115200
REMOTE   = "/tmp/ar8035-fix"
CHUNK    = 512   # bytes per hex line

AWK_DECODE = (
    b"awk 'BEGIN{h=\"0123456789abcdef\"}"
    b"{for(i=1;i<=length($0);i+=2)"
    b"printf\"%c\",(index(h,tolower(substr($0,i,1)))-1)*16+"
    b"(index(h,tolower(substr($0,i+1,1)))-1)}'"
)


def drain(ser, secs=0.5):
    deadline = time.monotonic() + secs
    buf = b''
    while time.monotonic() < deadline:
        n = ser.in_waiting
        if n:
            buf += ser.read(n)
        else:
            time.sleep(0.02)
    return buf


def wait_prompt(ser, timeout=15.0):
    buf = b''
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        n = ser.in_waiting
        if n:
            chunk = ser.read(n)
            buf += chunk
            if b'#' in chunk:
                time.sleep(0.2)
                buf += ser.read(ser.in_waiting or 0)
                return buf.decode('utf-8', errors='replace')
        else:
            time.sleep(0.05)
    return buf.decode('utf-8', errors='replace')


def cmd(ser, c, timeout=10.0):
    ser.write(c + b'\n')
    ser.flush()
    return wait_prompt(ser, timeout=timeout)


with open(BINARY, 'rb') as f:
    data = f.read()

expected_md5 = hashlib.md5(data).hexdigest()
print(f"[*] Binary: {len(data)} bytes, md5={expected_md5}")

with serial.Serial(UART, BAUD, timeout=0.5) as ser:
    ser.rts = False
    time.sleep(0.3)
    drain(ser)

    # Verify shell alive
    r = cmd(ser, b"echo alive")
    if 'alive' not in r:
        print(f"[!] Shell not responding: {r!r}")
        sys.exit(1)
    print(f"[+] Shell alive")

    # Clean up
    cmd(ser, f"rm -f {REMOTE}".encode(), timeout=5)

    # Start awk decoder
    awk_cmd = AWK_DECODE + f" > {REMOTE} 2>/dev/null\n".encode()
    ser.write(awk_cmd)
    ser.flush()
    time.sleep(0.8)
    drain(ser, 0.3)

    # Send chunks
    for offset in range(0, len(data), CHUNK):
        chunk = data[offset:offset + CHUNK]
        ser.write(binascii.hexlify(chunk) + b'\n')
        time.sleep(0.01)

    ser.flush()
    time.sleep(0.5)

    # EOF to terminate awk
    ser.write(b'\x04')
    ser.flush()
    time.sleep(2.0)
    wait_prompt(ser, timeout=10.0)
    drain(ser, 0.5)

    # Verify size and md5
    r = cmd(ser, f"wc -c {REMOTE}".encode())
    print(f"  wc -c: {r.strip()}")
    if str(len(data)) not in r:
        print(f"[!] Size mismatch -- expected {len(data)} bytes")
        sys.exit(1)

    r = cmd(ser, f"md5sum {REMOTE}".encode())
    print(f"  md5sum: {r.strip()}")
    if expected_md5 not in r:
        print(f"[!] MD5 mismatch -- transfer corrupted")
        sys.exit(1)

    print(f"[+] Transfer OK")

    # Make executable and run
    cmd(ser, f"chmod +x {REMOTE}".encode(), timeout=5)

    print(f"[*] Running {REMOTE} ...")
    ser.write(f"{REMOTE}\n".encode())
    ser.flush()
    time.sleep(3.0)
    r = wait_prompt(ser, timeout=15.0)
    print(r)
    drain(ser, 1.0)

    # Also check eth0 RX stats after
    print("[*] Checking eth0 rx_packets after fix...")
    r = cmd(ser, b"cat /proc/net/dev | grep eth0", timeout=5)
    print(r)
