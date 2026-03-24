# Troubleshooting

Organized by symptom. Each entry includes the likely cause, the fix, and a link to the relevant bug write-up or technical document.

---

## JTAG / OpenOCD Issues

### OpenOCD won't connect

**Symptom:** OpenOCD logs `JTAG scan chain interrogation failed: all ones` or `TDO seems stuck high`. The `halt` command never succeeds.

**Likely cause:** OpenOCD was started before the MR18 was powered on, or after the ~2-second Nandloader window closed. The EJTAG TAP is only alive during a narrow window after power-on.

**Fix:** The `mr18_flash.py` script automates the timing: it powers the MR18 on via the PSU, waits ~1.5 seconds, then launches OpenOCD. If the first attempt misses the window, it retries up to 6 times with automatic power cycling. If you are running manually, start OpenOCD approximately 1.5 seconds after powering on the MR18.

**Also check:**
- ESP-Prog USB connection (`ls /dev/ttyUSB*` -- see [Hardware Setup](hardware-setup.md#device-enumeration))
- JTAG wiring (TDI, TDO, TMS, TCK, GND all connected)
- TMS pull-up resistor present (4.7 kohm to 3.3V)

**Reference:** [Bug 2: OpenOCD Startup Timing](../bugs/bug-02-openocd-timing.md)

### CPU won't halt

**Symptom:** OpenOCD connects and detects the TAP, but `halt` does not succeed. The CPU continues running and the Nandloader disables JTAG probing.

**Likely cause:** The 2-second JTAG window was missed. The Nandloader disables JTAG probing approximately 2 seconds after power-on. If the `halt` command arrives even slightly late, the TAP stops responding.

**Fix:** The script retries automatically—up to 6 power-cycle attempts with slightly varied timing. Each cycle powers off, waits, powers on, starts OpenOCD, and attempts `halt`. If all 6 attempts fail, check that the PSU is actually cycling power (see the PSU troubleshooting section below).

**Reference:** [JTAG Timing Attack](../technical/jtag-timing-attack.md), [Bug 2](../bugs/bug-02-openocd-timing.md)

---

## Binary Loading Issues

### load_image completes too fast

**Symptom:** `load_image` reports completion in under 5 seconds for the 6.9 MB initramfs. At the PRACC transfer rate of ~97 KB/s, this should take approximately 70 seconds.

**Likely cause:** The OpenOCD telnet wrapper's `_drain()` method hit its socket timeout before `load_image` actually completed. The command appeared to succeed, but the data transfer was incomplete.

**Fix:** This bug is already fixed in the current `mr18_flash.py`. The `_drain()` method now uses a per-command timeout parameter (180 seconds for `load_image`). If you see this behavior, verify you are running the latest version of the script. Check the `_drain()` timeout in the `OCD` class.

**Reference:** [Bug 3: Socket Buffer Contamination](../bugs/bug-03-socket-contamination.md)

### XOR checksum mismatch

**Symptom:** After `load_image` completes (correct duration), the XOR checksum does not match the expected value.

**Likely cause:** PRACC bit errors during the JTAG transfer. The MIPS EJTAG Processor Access handshake has an inherent error rate of approximately 0.04% (1 corrupted word per 2500 words). Over a 6.9 MB binary, this produces roughly 60 corrupted words per load.

**Fix:** This is expected and handled automatically. The `cpu_scan_and_fix` phase scans all 847 x 8 KB chunks using CPU-executed XOR, identifies corrupt chunks, rewrites them from the file, and re-verifies. The script only launches the kernel after both the chunk scan and a final full-binary XOR pass.

If `cpu_scan_and_fix` itself fails after 3 rewrite attempts on a chunk, the script aborts and retries from a fresh power cycle.

**Reference:** [Bug 4: PRACC Write Bit Errors](../bugs/bug-04-pracc-bit-errors.md)

---

## Boot / Decompression Issues

### lzma-loader reports "data error!"

**Symptom:** The kernel launches, UART shows `LZMA data, decompressing...`, followed by `data error!`. The kernel never boots.

**Likely cause (most common):** D-cache stale data. The Cisco Nandloader filled the D-cache with dirty lines at the same physical addresses where our OpenWrt binary was loaded. When the lzma-loader reads via KSEG0 (cached), it gets the stale Cisco data instead of our binary.

**Fix:** The D-cache flush must run **before** `load_image`, not after. The current script does this correctly (Phase 0: pre-load flush). If you see this error, verify that the pre-flush SDBBP is hitting correctly:

```
[+] D-cache pre-flush done [pass] -- dirty Cisco lines evicted before load
```

If the pre-flush fails or reports a wrong PC, the trampoline program was corrupted during write.

**Other causes:**
- **XOR cancellation** ([Bug 10](../bugs/bug-10-xor-cancellation.md)): Two PRACC bit errors with identical XOR deltas cancel each other in the full-binary checksum, making it appear correct. The chunk scan catches these because the probability of two errors in the same 8 KB chunk is very low.
- **Flush ordering** ([Bug 13](../bugs/bug-13-flush-ordering.md)): If the flush runs after `load_image`, the dirty D-cache write-back overwrites the freshly loaded binary. The flush must run before the load.

**Reference:** [Bug 11: D-Cache Stale Data](../bugs/bug-11-dcache-stale-data.md), [Bug 13: Flush Ordering](../bugs/bug-13-flush-ordering.md), [Bug 10: XOR Cancellation](../bugs/bug-10-xor-cancellation.md)

---

## Network / Failsafe Issues

### Kernel boots but 192.168.1.1 doesn't respond

**Symptom:** UART shows the kernel booting successfully (Linux banner, driver initialization messages), but `ping 192.168.1.1` gets no response. `nmap` or `arping` show no device on the subnet.

**Likely cause:** Failsafe mode was not triggered. Without failsafe, OpenWrt's preinit mounts the Meraki NAND overlay, which replaces the networking configuration with Cisco's settings. The device acquires an IP via DHCP as a WAN client instead of coming up at `192.168.1.1`.

**Diagnostic:** Check the UART console output for Meraki management frame references or DHCP client activity. If you see ethertype `0x0642` frames on `tcpdump`, the Meraki overlay is active.

**Fix:** The device must be re-flashed with failsafe mode properly triggered. Retry `mr18_flash.py` from scratch. Verify the UART `f` key trigger is working (see next entry).

**Reference:** [Bug 15: Meraki NAND Overlay](../bugs/bug-15-meraki-nand-overlay.md)

### Failsafe mode doesn't trigger

**Symptom:** The kernel boots, UART shows preinit messages, but failsafe mode is never activated. The preinit proceeds to mount the NAND overlay normally.

**Likely cause:** The UART `f` key was not sent at the right time, and the EN pin did not pull GPIO17 LOW during the preinit window.

**Diagnostic steps:**

1. **Check UART output** for the failsafe prompt. You should see:
   ```
   Press the [f] key and hit [enter] to enter failsafe mode
   ```
   Followed by the script's response:
   ```
   [+] Sent 'f\n' to UART at t=22.1s -- triggering failsafe!
   ```
   If the prompt appears but no `f` was sent, the UART thread may not be running or the serial port may be in use by another process.

2. **Check EN wiring**: Verify the EN pin from the ESP-Prog UART connector is connected to the non-GND pad of the reset button (GPIO17 net). Verify there is no series resistor (see [Bug 22](../bugs/bug-22-resistor-wrong-side.md)).

3. **Check timing**: The EN pin should be asserted from t=2s after kernel launch and held for 40 seconds. If the `FAILSAFE_EN_DELAY` or `FAILSAFE_EN_HOLD` constants were modified, the window may not cover the preinit phase.

4. **Check serial port availability**: `lsof /dev/ttyUSB4` should show only the `mr18_flash.py` process. If another process (minicom, screen, etc.) has the port open, the script cannot send commands.

**Reference:** [Failsafe Trigger](../technical/failsafe-trigger.md), [Bug 22](../bugs/bug-22-resistor-wrong-side.md), [Bug 23](../bugs/bug-23-en-before-boot.md)

---

## Ethernet Issues

### Ethernet TX works but RX is broken

**Symptom:** After OpenWrt boots from NAND, the MR18 can send packets (ARP requests visible on the host) but cannot receive any. `rx_packets` stays at zero. `ethtool -S eth0` shows FCS errors incrementing on every received frame. `ping` fails in both directions despite the link being physically up at 1 Gbps.

**Likely cause:** The AR8035 PHY's RGMII RX internal clock delay is not enabled. The OpenWrt device tree specifies `phy-mode = "rgmii"` instead of `"rgmii-rxid"`, so the `at803x` kernel driver leaves the delay at its hardware default (disabled). Without the 2 ns RX clock delay, the MAC samples data at transition points, producing garbage.

**Fix:** Run the `ar8035-fix` binary:

```sh
cd ar8035-fix/
python3 send_binary.py
```

This writes MDIO debug register 0x00 bit 15 to enable the RGMII RX clock delay. The fix takes effect immediately -- `rx_packets` will begin incrementing.

For persistence across reboots, install the hotplug script (see [Quick Start Guide—Step 10](quickstart.md#step-10-install-hotplug-script-for-persistence)).

**Reference:** [AR8035 PHY Fix](../technical/ar8035-phy-fix.md)

### ar8035-fix crashes with "Illegal instruction"

**Symptom:** Running the `ar8035-fix` binary on the MR18 produces an `Illegal instruction` signal and the process terminates immediately, before printing any output.

**Likely cause:** The binary was compiled with hardware floating-point instructions. The QCA9557/AR9344 SoC has no hardware FPU. Any FPU instruction (including those generated by glibc's `crt0.o` startup code for saving/restoring FP registers) causes an illegal instruction trap.

**Fix:** The binary must be compiled with `-msoft-float -nostdlib -nostartfiles`. The included `Makefile` and pre-built binary already use these flags. If you rebuilt the binary, verify the Makefile flags:

```makefile
CFLAGS  = -O2 -msoft-float -mno-abicalls -fno-pic
LDFLAGS = -nostdlib -nostartfiles -Wl,-z,noexecstack -Wl,-e,_start -static
```

The most common mistake is linking against glibc (forgetting `-nostdlib`), which pulls in `crt0.o` containing FPU instructions.

**Reference:** [AR8035 PHY Fix—Compilation](../technical/ar8035-phy-fix.md#compilation)

---

## Device Stability Issues

### Device reboots every ~90 seconds

**Symptom:** The MR18 boots into OpenWrt (or failsafe mode), then spontaneously reboots approximately 90 seconds later. This repeats in a loop.

**Likely cause:** The QCA9557 hardware watchdog timer. The Nandloader enables the watchdog with a ~90-second timeout. If nothing feeds the watchdog after boot, it triggers a full SoC reset.

In normal OpenWrt operation, `procd` feeds the watchdog. In failsafe mode, however, the watchdog feeder may not start automatically.

**Fix:** The `mr18_flash.py` script handles this by sending a watchdog kicker command over UART as soon as the failsafe shell is detected:

```sh
( while true; do echo 1 > /dev/watchdog; sleep 5; done ) &
```

If you are in a manual session and the device keeps rebooting, run this command immediately after getting a shell.

**Reference:** [Bug 5: Hardware Watchdog](../bugs/bug-05-hardware-watchdog.md)

---

## PSU / Power Cycling Issues

### PSU commands not working

**Symptom:** The script attempts power cycling, but the MR18 never actually turns off or on. The UART shows no reset activity. The CPU PC is at an old `SDBBP` address from a previous session.

**Likely cause:** The named pipe used to communicate with `scpi-repl` sent an EOF, causing `scpi-repl` to exit its read loop. This happens when the pipe is opened in write mode (`"w"`) instead of append mode (`"a"`).

**Diagnostic:** Check the `scpi-repl` process:

```sh
ps aux | grep scpi-repl
```

If `scpi-repl` is not running, the pipe reader has died.

**Fix:** This bug is already fixed in the current `mr18_flash.py`. The `psu()` function uses append mode (`open(PSU_PIPE, 'a')`) which does not send EOF on close. If you see this behavior, verify the `psu()` function uses `'a'` mode, not `'w'` mode.

**Reference:** [Bug 6: Named Pipe EOF](../bugs/bug-06-named-pipe-eof.md)

### Device never power-cycles

**Symptom:** The script says it is power cycling, but the MR18 LEDs never go off. The device stays running continuously.

**Likely cause:** One of:

1. **scpi-repl not running:** The process may have exited due to a pipe EOF, USB disconnection, or instrument error.
2. **Named pipe does not exist:** `/tmp/scpi_pipe` was deleted or never created.
3. **PSU USB disconnected:** The SCPI instrument is no longer reachable.

**Diagnostic steps:**

```sh
# 1. Is scpi-repl running?
ps aux | grep scpi-repl

# 2. Does the named pipe exist?
ls -la /tmp/scpi_pipe

# 3. Check scpi-repl log for errors
tail -20 /tmp/scpi_repl.log

# 4. Is the PSU USB device present?
lsusb | grep -i <your-psu-vendor>
```

**Fix:** Restart `scpi-repl` if it has died. If using a manual PSU, toggle power by hand when the script prompts.

---

## UART Transfer Issues

### uart_transfer.py pre-test fails

**Symptom:** The 32-byte pre-test reports `Pre-test FAIL: expected 32 bytes` or an MD5 mismatch.

**Likely cause:** The awk hex decoder on the MR18 side is not working correctly. Possible reasons:

1. **Shell not responsive:** The MR18 has rebooted or the shell has hung.
2. **UART corruption:** Baud rate mismatch or wiring issue.
3. **Another process on /dev/ttyUSB4:** Only one process can have the serial port open.

**Fix:** Verify the MR18 is alive and the shell is responsive. Check that no other process has `/dev/ttyUSB4` open (`lsof /dev/ttyUSB4`). Verify baud rate is 115200.

### UART transfer MD5 mismatch

**Symptom:** The full transfer completes but the MD5 does not match. The transfer took the expected ~20 minutes.

**Likely cause:** One or more hex lines were corrupted during UART transmission. At 115200 baud over ~20 minutes, even a very low error rate can corrupt a few bytes.

**Fix:** Retry the transfer. The pre-test should still pass (it tests the awk decoder, not the long-duration link). If repeated transfers fail, check for electrical noise on the UART wiring, try shorter wires, or reduce the baud rate.

---

## Checking Logs

When troubleshooting, always check these log sources:

### /tmp/openocd.log

Contains all OpenOCD output: JTAG scan chain interrogation, TAP detection, `load_image` progress, memory read/write results, and error messages.

```sh
tail -50 /tmp/openocd.log
```

Key things to look for:
- `tap/device found` -- JTAG chain detected successfully
- `target halted` -- CPU halt succeeded
- `downloaded X bytes` -- load_image transfer progress
- `Error:` lines—any OpenOCD error

### /tmp/scpi_repl.log

Contains scpi-repl instrument discovery output and command responses.

```sh
tail -20 /tmp/scpi_repl.log
```

Key things to look for:
- `eset>` prompt—scpi-repl is ready and connected to the instrument
- Voltage/current readbacks confirming PSU commands were executed
- Error messages if the instrument disconnected

### UART console output

The `mr18_flash.py` script prints all UART console output prefixed with `[uart t=Xs]`. This shows:

- lzma-loader decompression progress and any errors
- Kernel boot messages
- Preinit failsafe prompt and response
- Shell setup commands (watchdog kicker, ifconfig, telnetd)
- Any error messages from the MR18

If the script is not running, you can monitor the UART console directly:

```sh
screen /dev/ttyUSB4 115200
# or
minicom -D /dev/ttyUSB4 -b 115200
```

Press `Ctrl-A` then `K` to exit screen, or `Ctrl-A` then `X` to exit minicom.

---

## Cross-references

- [Hardware Setup](hardware-setup.md) -- wiring and connection verification
- [Quick Start Guide](quickstart.md) -- the expected happy-path flow
- [Script Reference](../reference/script-reference.md) -- configurable constants and usage
- [Bug Index](../bugs/index.md) -- full list of all documented bugs
- [JTAG Timing Attack](../technical/jtag-timing-attack.md) -- details on the Nandloader timing window
- [D-Cache Stale Data](../technical/mips-memory-model.md) -- MIPS memory model and cache coherency
- [Failsafe Trigger](../technical/failsafe-trigger.md) -- all five failsafe approaches and why only two work
