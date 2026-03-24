# Quick Start Guide

Step-by-step from "hardware wired" to "OpenWrt running from NAND with bidirectional Ethernet." Assumes all hardware is connected per the [Hardware Setup](hardware-setup.md) guide and all software is installed per [Prerequisites](prerequisites.md).

**Total expected runtime: 30--40 minutes** (including ~5 min JTAG flash, ~20 min UART sysupgrade transfer, ~5 min AR8035 fix and verification).

---

## Step 1: Download firmware

If you have not already downloaded the firmware images, fetch them now:

```sh
cd firmware/
wget https://downloads.openwrt.org/releases/25.12.0/targets/ath79/nand/openwrt-25.12.0-ath79-nand-meraki_mr18-initramfs-kernel.bin
wget https://downloads.openwrt.org/releases/25.12.0/targets/ath79/nand/openwrt-25.12.0-ath79-nand-meraki_mr18-squashfs-sysupgrade.bin
```

Verify integrity:

```sh
echo "db191ecf0224f030365d604aa3919da9  openwrt-25.12.0-ath79-nand-meraki_mr18-initramfs-kernel.bin" | md5sum -c
echo "53e272bed2041616068c6958fe28a197  openwrt-25.12.0-ath79-nand-meraki_mr18-squashfs-sysupgrade.bin" | md5sum -c
```

Both should print `OK`. See [Prerequisites—Firmware Downloads](prerequisites.md#firmware-downloads) for details.

## Step 2: Verify ESP-Prog detected

```sh
ls /dev/ttyUSB*
```

You should see at least `ttyUSB0`, `ttyUSB1` (JTAG, Interface A), and `ttyUSB4` (UART, Interface B). If not, check the USB cable and `lsusb` output. See [Hardware Setup—Device Enumeration](hardware-setup.md#device-enumeration).

## Step 3: Start scpi-repl (if using programmable PSU)

If you have a SCPI-capable bench PSU, start `scpi-repl` so the flash script can control power cycling:

```sh
scpi-repl
```

The `mr18_flash.py` script will start and manage `scpi-repl` automatically. If you are using a manual PSU, no action is needed here—you will toggle power by hand when prompted. See [Script Reference—scpi-repl](../reference/script-reference.md).

## Step 4: Run the JTAG flash script

```sh
cd jtag/
sudo python3 mr18_flash.py
```

The script requires root (or appropriate permissions) for NIC configuration and OpenOCD. It executes the following phases automatically:

### Phase 0: D-cache flush (pre-load)

```
[*] Flushing D-cache before load (cache 0x01 Index_WB_Invalidate, all 4 ways) ...
[+] D-cache pre-flush done [pass] -- dirty Cisco lines evicted before load
```

The CPU executes a 128 KB KSEG0 sweep to evict dirty D-cache lines left by the Cisco Nandloader. You will see an `SDBBP halt` message when the flush program completes. This prevents stale Cisco data from overwriting the OpenWrt binary later (see [Bug 11](../bugs/bug-11-dcache-stale-data.md), [Bug 13](../bugs/bug-13-flush-ordering.md)).

### Phase 1: load_image (~70 seconds)

```
[*] Loading 6768 KB -> 0xa005FC00  (est. 70s at 1000 kHz) ...
    load_image done in 71.3s
```

The initramfs kernel (6.9 MB) is transferred from the host to MR18 RAM via JTAG PRACC at approximately 97 KB/s. Progress is visible in `/tmp/openocd.log`. If `load_image` completes in under 5 seconds, something went wrong—see [Troubleshooting](troubleshooting.md#load_image-completes-too-fast).

### Phase 2: Post-load D-cache flush

```
[*] Post-load D-cache flush (cache 0x01 Index_WB_Invalidate, all 4 ways) ...
[+] Post-load D-cache flush done [pass]
```

A second D-cache flush as a safety measure. Belt-and-suspenders against any speculative cache fills during the PRACC transfer.

### Phase 3: XOR checksum + cpu_scan_and_fix (~60 seconds)

```
[*] Running XOR checksum (14-word MIPS program, ~25ms CPU time) ...
[+] Full XOR match: 0xf524142e [pass]
[*] Running cpu_scan_and_fix (CPU-XOR chunk scan, no bulk PRACC reads) ...
  ... 100/847 chunks scanned, 0 bad so far
  ... 200/847 chunks scanned, 0 bad so far
  ...
  ... 847/847 chunks scanned, 2 bad so far
[!] 2 corrupt chunk(s) -- rewriting entirely from file ...
  [+] Chunk 312/847 (file+0x26c000) fixed [pass]
  [+] Chunk 615/847 (file+0x4cc000) fixed [pass]
[+] All 2 corrupt chunk(s) rewritten and CPU-XOR verified [pass]
[*] Final full XOR checksum after chunk scan ...
[+] Final XOR MATCH: 0xf524142e [pass]
```

The CPU runs a 14-word MIPS XOR checksum program to verify the loaded binary. Then `cpu_scan_and_fix` scans all 847 x 8 KB chunks individually using CPU-executed XOR. Any chunks with PRACC bit errors (typically 0--5 out of 847) are rewritten from the file and re-verified. A final full-binary XOR confirms everything is consistent. See [Bug 4](../bugs/bug-04-pracc-bit-errors.md) and [Bug 10](../bugs/bug-10-xor-cancellation.md).

### Phase 4: Kernel launch + failsafe trigger

```
[*] Resuming at 0xa0800000 -> lzma-loader at 0xa0060000 ...
============================================================
[+] Initramfs kernel launched! Waiting for OpenWrt to boot ...
============================================================
[+] EN asserted at t=2.0s -- GPIO17 LOW via NPN (holding 40s) ...
  [uart t= 3.2s] U-Boot lzma-loader ...
  [uart t=16.4s] LZMA data OK
  [uart t=22.1s] Press the [f] key and hit [enter] to enter failsafe mode
  [+] Sent 'f\n' to UART at t=22.1s -- triggering failsafe!
  [uart t=25.8s] failsafe mode activated ...
  [+] Failsafe shell setup sent at t=26.3s (watchdog kicker + ifconfig + telnetd)
```

The CPU resumes at the launch trampoline which jumps to the lzma-loader. LZMA decompression takes approximately 13 seconds. The UART thread watches for the preinit failsafe prompt and sends `f\n` to trigger failsafe mode. The EN pin is also held LOW as a backup. After failsafe is confirmed, the script sends commands over UART to start the watchdog kicker, configure `eth0` to `192.168.1.1`, and start `telnetd`.

### Phase 5: Wait for 192.168.1.1

```
[*] Configuring host NIC ...
[*] Waiting for 192.168.1.1 to respond ...
[+] 192.168.1.1 is up!
```

The script configures the host Ethernet NIC and polls until the MR18 responds at `192.168.1.1`. This typically takes 30--90 seconds after kernel launch.

## Step 5: Verify failsafe mode

If the script reports success, the MR18 is in OpenWrt failsafe mode at `192.168.1.1`:

```sh
ping 192.168.1.1          # Should get replies
telnet 192.168.1.1 23     # Should get a root shell (no password)
```

At this point the device is running the initramfs kernel from RAM. The NAND flash still contains Cisco firmware. The next step writes OpenWrt to NAND permanently.

## Step 6: Transfer sysupgrade via UART (~20 minutes)

Open a new terminal and run the UART transfer script:

```sh
cd ar8035-fix/
python3 uart_transfer.py
```

Expected output:

```
[*] Poking MR18 shell ...
[+] Shell alive: 'echo alive\r\nalive\r\n# '
[*] Pre-test: sending 32 known bytes through awk decoder ...
  wc -c: 32 /tmp/awk_test.bin
  md5sum: <hash>  /tmp/awk_test.bin
[+] Pre-test PASS: md5 <hash> [pass]

[*] Pre-test passed -- proceeding with full transfer.
[*] ETA ~1200s at 115200 baud
[*] Transferring 7,077,888 bytes, chunk=512B (1024 hex chars/line) ...
    5%  345KB/7077KB  5.6KB/s  ETA 1150s  (echo drained 320KB)
   10%  691KB/7077KB  5.6KB/s  ETA 1070s  (echo drained 645KB)
  ...
  100%  7077KB/7077KB  5.6KB/s  ETA 0s  (echo drained 6800KB)
[+] Hex sent: 7,077,888 bytes in 1203.4s
[*] Checking file size ...
[*] Verifying md5sum ...
[+] MD5 MATCH [pass] -- running sysupgrade ...
```

The script hex-encodes the sysupgrade image and sends it line-by-line over the UART serial connection at 115200 baud. An awk decoder on the MR18 converts it back to binary. At ~5.6 KB/s effective throughput, the transfer takes approximately 15--20 minutes. After MD5 verification, it automatically runs `sysupgrade /tmp/fw.bin`.

## Step 7: Wait for reboot from NAND (~90 seconds)

After `sysupgrade` completes, the MR18 reboots and boots OpenWrt from NAND. This takes approximately 90 seconds.

```sh
# Wait for the device to come back up
ping 192.168.1.1
```

The device is now running OpenWrt from NAND flash. However, Ethernet RX is broken at this point due to the AR8035 PHY RGMII RX clock delay bug—you can transmit but not receive. The next steps fix this.

## Step 8: Build AR8035 fix (if needed)

A pre-built `ar8035-fix` binary (5592 bytes, MIPS32 big-endian static ELF) is included in the repository. Skip this step unless you have modified the C or assembly source.

```sh
cd ar8035-fix/
make           # Requires mips-linux-gnu-gcc cross-compiler
```

Or use the Docker-based build (works on any Linux distro):

```sh
make docker
```

See [Prerequisites—Cross-Compiler](prerequisites.md#cross-compiler-ar8035-fix-only) for cross-compiler installation.

## Step 9: Deploy AR8035 fix

Transfer the `ar8035-fix` binary to the MR18 and execute it:

```sh
cd ar8035-fix/
python3 send_binary.py
```

Expected output:

```
[*] Binary: 5592 bytes, md5=<hash>
[+] Shell alive
  wc -c: 5592 /tmp/ar8035-fix
  md5sum: <hash>  /tmp/ar8035-fix
[+] Transfer OK
[*] Running /tmp/ar8035-fix ...
AR8035 PHY fix: RX delay enabled, hibernation disabled
[*] Checking eth0 rx_packets after fix...
eth0: ... rx_packets:42 ...
```

The binary writes two MDIO debug registers on the AR8035 PHY:

1. Disables hibernation mode (debug register 0x0B, clear bit 15)
2. Enables RGMII RX clock delay (debug register 0x00, set bit 15)

After running, bidirectional Ethernet should work immediately. See [AR8035 PHY Fix](../technical/ar8035-phy-fix.md).

## Step 10: Install hotplug script for persistence

The AR8035 PHY resets to default register values on every power cycle. A hotplug script ensures the fix is reapplied automatically whenever the LAN interface comes up.

First, copy the binary to a persistent location:

```sh
# Via UART (send_binary.py already put it at /tmp/ar8035-fix)
# From the MR18 shell:
cp /tmp/ar8035-fix /usr/bin/ar8035-fix
chmod +x /usr/bin/ar8035-fix
```

Then create the hotplug script:

```sh
cat > /etc/hotplug.d/iface/10-ar8035-fix << 'HOTPLUG_EOF'
#!/bin/sh
[ "$ACTION" = "ifup" ] && [ "$INTERFACE" = "lan" ] && /usr/bin/ar8035-fix
HOTPLUG_EOF
chmod +x /etc/hotplug.d/iface/10-ar8035-fix
```

This script runs `/usr/bin/ar8035-fix` every time the `lan` interface comes up, ensuring the RGMII RX delay is enabled before any network traffic flows.

## Step 11: Verification checklist

Run through these checks to confirm everything is working:

```sh
# 1. Ping the device
ping 192.168.1.1
# Should get replies (proves bidirectional Ethernet)

# 2. SSH into the device
ssh root@192.168.1.1
# Should get a root shell (default: no password)

# 3. Check LuCI web interface
# Open http://192.168.1.1 in a browser
# Should see the OpenWrt LuCI status page

# 4. Verify zero FCS errors on Ethernet
ethtool -S eth0 | grep -i fcs
# rx_fcs_errors should be 0

# 5. Verify RX packets are incrementing
cat /proc/net/dev | grep eth0
# rx_packets should be non-zero and increasing

# 6. Verify the hotplug script is in place
cat /etc/hotplug.d/iface/10-ar8035-fix
# Should show the script content

# 7. Verify ar8035-fix binary is persistent
ls -la /usr/bin/ar8035-fix
# Should show the binary (5592 bytes)
```

If all checks pass, the MR18 is fully operational with OpenWrt and bidirectional Ethernet. The device will survive power cycles and maintain the AR8035 fix through the hotplug script.

---

## Runtime Summary

| Phase | Duration | Description |
|-------|----------|-------------|
| PSU + OpenOCD init | ~5 s | Start scpi-repl, detect instruments |
| Power cycle + halt | ~5 s per attempt | Timing attack on Nandloader JTAG window |
| D-cache pre-flush | < 1 s | Evict stale Cisco D-cache lines |
| Binary load (PRACC) | ~70 s | 6.9 MB at ~97 KB/s over JTAG |
| D-cache post-flush | < 1 s | Belt-and-suspenders second flush |
| XOR checksum | < 1 s | CPU-executed full-binary XOR |
| Chunk scan + fix | ~60 s | 847 x 8 KB CPU-XOR scan, rewrite bad chunks |
| Final XOR verify | < 1 s | Consistency confirmation |
| Kernel boot + failsafe | ~30 s | LZMA decompress + kernel init + preinit |
| Network wait | ~30--90 s | Poll until 192.168.1.1 responds |
| **JTAG flash subtotal** | **~3--5 min** | |
| UART sysupgrade transfer | ~15--20 min | Hex-encoded transfer at 115200 baud |
| Reboot from NAND | ~90 s | Sysupgrade writes + reboot |
| AR8035 fix + deploy | ~1 min | Transfer binary, execute, verify |
| Hotplug script install | < 1 min | Copy binary, create script |
| **Total** | **~30--40 min** | |

## Cross-references

- [Hardware Setup](hardware-setup.md) -- physical wiring guide
- [Prerequisites](prerequisites.md) -- software dependencies and firmware downloads
- [Script Reference](../reference/script-reference.md) -- detailed CLI usage for all scripts
- [Troubleshooting](troubleshooting.md) -- symptom-based debugging guide
- [AR8035 PHY Fix](../technical/ar8035-phy-fix.md) -- technical details of the RGMII fix
- [Failsafe Trigger](../technical/failsafe-trigger.md) -- how failsafe mode is triggered
