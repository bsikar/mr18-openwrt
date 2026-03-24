# OpenOCD Configuration

How OpenOCD is configured, started, and controlled for JTAG communication with the AR9344 on the Meraki MR18. Covers both configuration files and the telnet command interface used by `mr18_flash.py`.

## Configuration Files

OpenOCD uses two configuration files, loaded in order. The first configures the debug adapter hardware. The second configures the target chip.

### esp-prog.cfg -- Debug Adapter Interface

Location: `jtag/esp-prog.cfg`

```
adapter driver ftdi
ftdi vid_pid 0x0403 0x6010
ftdi channel 0
ftdi layout_init 0x0008 0x000b
reset_config none
adapter speed 1000
```

Line-by-line:

**`adapter driver ftdi`** -- Use the FTDI driver. The ESP-Prog module contains an FT2232H, which is a dual-channel USB-to-serial/JTAG chip. The FTDI driver speaks the MPSSE (Multi-Protocol Synchronous Serial Engine) protocol to bit-bang the JTAG signals.

**`ftdi vid_pid 0x0403 0x6010`** -- USB vendor ID `0x0403` (FTDI) and product ID `0x6010` (FT2232H). This tells OpenOCD which USB device to claim.

**`ftdi channel 0`** -- Use channel 0 (interface A) of the FT2232H for JTAG. Channel 1 (interface B) is the UART interface, used separately for console output and the EN/reset line. Channel 0 carries TDI, TDO, TCK, and TMS.

**`ftdi layout_init 0x0008 0x000b`** -- Initial state and direction of the FT2232H GPIO pins. The two arguments are `(data, direction)`:
- `0x0008` = data: bit 3 high (nTRST deasserted = inactive)
- `0x000b` = direction: bits 0,1,3 are outputs (TCK, TDI, nTRST); bit 2 (TDO) is input

This ensures nTRST starts deasserted so the JTAG TAP is not held in reset.

**`reset_config none`** -- No reset lines are connected. The MR18 does not expose SRST (system reset) on its JTAG header. We use the bench power supply to power-cycle instead.

**`adapter speed 1000`** -- JTAG clock at 1000 kHz (1 MHz). This is conservative for the AR9344 (which supports higher), but empirically reliable over the ESP-Prog's flying wires. Higher speeds increase the risk of signal integrity errors on the PRACC-heavy load operations.

### mr18.cfg -- Target Configuration

Location: `jtag/mr18.cfg`

```tcl
if { [info exists CHIPNAME] } {
        set _CHIPNAME $_CHIPNAME
} else {
        set _CHIPNAME ar9344
}

if { [info exists CPUTAPID] } {
        set _CPUTAPID $CPUTAPID
} else {
        set _CPUTAPID 0x00000001
}

jtag newtap $_CHIPNAME cpu -irlen 5 -expected-id $_CPUTAPID

set _TARGETNAME $_CHIPNAME.cpu
target create $_TARGETNAME mips_m4k -endian big -chain-position $_TARGETNAME

$_TARGETNAME configure -work-area-phys 0x81000000 -work-area-size 0x4000 -work-area-backup 1

transport select jtag
adapter speed 1000
```

Line-by-line:

**`set _CHIPNAME ar9344`** -- Internal name for the chip. Used as a prefix for TAP and target names (e.g., `ar9344.cpu`). The AR9344 and QCA9557 share the same MIPS core and JTAG interface.

**`set _CPUTAPID 0x00000001`** -- The expected JTAG IDCODE. This is **non-standard**: the JTAG specification (IEEE 1149.1) requires bit 0 of the IDCODE to be 1, which `0x00000001` satisfies, but a conforming IDCODE also encodes manufacturer ID, part number, and version in the remaining bits. The QCA9557 returns `0x00000001` for all of those fields -- a minimal valid IDCODE that provides no manufacturer identification. This is what the chip actually returns; it is not configurable.

**`jtag newtap $_CHIPNAME cpu -irlen 5 -expected-id $_CPUTAPID`** -- Declare a TAP (Test Access Port) named `ar9344.cpu` with:
- `-irlen 5`: Instruction Register length is 5 bits (standard for MIPS EJTAG)
- `-expected-id 0x00000001`: OpenOCD will verify this IDCODE during `init` and refuse to proceed if the scan chain returns a different value

**`target create $_TARGETNAME mips_m4k -endian big -chain-position $_TARGETNAME`** -- Create a debug target:
- `mips_m4k`: OpenOCD's MIPS M4K/M14K target driver, which implements EJTAG protocol handling, PRACC memory access, register read/write, and breakpoint support
- `-endian big`: AR9344 runs in big-endian mode (MIPS can be either; this chip is configured big-endian by the Meraki firmware)
- `-chain-position ar9344.cpu`: Associates the target with the TAP declared above

**`-work-area-phys 0x81000000 -work-area-size 0x4000 -work-area-backup 1`** -- Configures a 16 KB work area in RAM that OpenOCD can use for fast bulk operations:
- `0x81000000` is KSEG0 (cached), physical `0x01000000` (16 MB into RAM)
- OpenOCD backs up the work area contents before use and restores them after (`-work-area-backup 1`)
- The work area is used internally by operations like `load_image` for PRACC acceleration

**`transport select jtag`** -- Explicitly select JTAG transport. The FT2232H also supports SWD (Serial Wire Debug), but MIPS uses JTAG. Being explicit avoids ambiguity.

**`adapter speed 1000`** -- Repeated here to ensure 1 MHz even if the interface config set a different speed.

## Starting OpenOCD

OpenOCD is started by `mr18_flash.py` as a subprocess:

```python
proc = subprocess.Popen([
    "openocd",
    "-f", f"{CFG_DIR}/esp-prog.cfg",
    "-f", f"{CFG_DIR}/mr18.cfg",
    "-c", "init",
])
```

The `-c init` flag is critical. It tells OpenOCD to:

1. Open the FTDI USB interface
2. Scan the JTAG chain (shift IDCODE out of each TAP)
3. Verify the IDCODE matches `0x00000001`
4. Examine the MIPS target (read EJTAG implementation register, detect debug features)
5. Start the telnet server on port 4444
6. Start the GDB server on port 3333 (not used by this project)

**OpenOCD must start AFTER the MR18 is powered on.** If OpenOCD starts before power-on, the JTAG scan chain finds no devices (all lines are floating) and the TAP scan fails. This was Bug 2. The script power-cycles the MR18 first, waits 1.5 seconds for the Nandloader to be active, then starts OpenOCD against the live TAP.

## The Telnet Interface

`mr18_flash.py` communicates with OpenOCD via a TCP telnet connection on port 4444. The `OCD` class wraps this:

```python
class OCD:
    def connect(self, retries=20) -> bool:
        # Connect to 127.0.0.1:4444 with up to 20 retries at 200ms intervals
    def cmd(self, command: str, timeout=5.0) -> str:
        # Send command, drain response until ">" prompt
    def close(self):
        # Close socket
```

Commands are sent as newline-terminated strings. Responses are read until the `>` prompt character appears, indicating OpenOCD is ready for the next command.

## Common OpenOCD Commands Used

### CPU Control

| Command | Purpose |
|---------|---------|
| `halt` | Request CPU halt via EJTAG debug exception |
| `wait_halt 300` | Block until CPU enters debug mode (300ms timeout) |
| `resume 0xa0800000` | Resume execution at a specific address |
| `reg pc` | Read the program counter |
| `reg` | Dump all CPU registers |

### Memory Access

| Command | Purpose |
|---------|---------|
| `mww 0xaddr 0xval` | Memory Write Word -- write 32-bit value to address |
| `mdw 0xaddr` | Memory Display Word -- read 32-bit value from address |
| `load_image /path/file 0xaddr bin` | Bulk load binary file to memory address |
| `dump_image /path/file 0xaddr size` | Bulk read memory to file |

### JTAG Primitives

| Command | Purpose |
|---------|---------|
| `jtag arp_init` | Re-scan JTAG chain (useful after power cycle without restarting OpenOCD) |
| `ar9344.cpu arp_examine` | Re-examine target (re-read EJTAG implementation register) |
| `irscan ar9344.cpu 0x0a` | Shift value into Instruction Register (0x0a = EJTAG control) |
| `drscan ar9344.cpu 32 0x9008` | Shift 32-bit value through Data Register |

The `irscan`/`drscan` commands are used for the raw EJTAG halt sequence when the high-level `halt` command fails (see [JTAG Timing Attack](jtag-timing-attack.md)).

## PRACC Mechanism

All memory accesses through OpenOCD on a MIPS EJTAG target use the **PRACC** (Processor Access) mechanism. PRACC does not perform DMA. Instead, it feeds MIPS instructions to the CPU one at a time through the EJTAG TAP:

1. OpenOCD places a MIPS instruction in the EJTAG data register
2. The CPU (in debug mode) fetches and executes that instruction
3. If the instruction performs a memory access, the data passes through the EJTAG data register
4. OpenOCD reads or writes the data register to complete the operation

This means every single 32-bit memory write requires approximately **30 JTAG transactions** (instruction feed + data transfer + handshake). At 1 MHz JTAG clock:

| Operation | Throughput |
|-----------|-----------|
| Write (mww / load_image) | ~97 KB/s |
| Read (mdw / dump_image) | ~3.5 KB/s |

For the 6.9 MB initramfs binary, `load_image` takes approximately **70 seconds** at this speed. Read-back verification via `dump_image` would take approximately **33 minutes** for the full binary, which is why the project uses CPU-executed XOR checksums instead of full readback.

### PRACC Control Errors

PRACC is fragile. Timing glitches on the JTAG bus can cause the CPU and OpenOCD to lose synchronization on the PRACC handshake. When this happens, OpenOCD reports "PRACC control error" and the current operation aborts. The script handles this with:

- Small chunk sizes (8 KB) for verification operations to limit the blast radius of a single error
- Retry logic (up to 4 attempts per chunk)
- CPU-executed verification (XOR checksums) instead of PRACC reads where possible

## Adapter Speed Considerations

The adapter speed of 1000 kHz is a balance between throughput and reliability:

- **Higher speeds** (2000+ kHz) reduce the load time proportionally but increase the risk of PRACC control errors, especially with flying wires and no proper impedance matching. The ESP-Prog's JTAG signals travel through ~15cm of jumper wires to the MR18's JTAG header.

- **Lower speeds** (500 kHz and below) are more reliable but extend the load time to over 2 minutes, increasing the risk of external interference (thermal throttling, watchdog timers, etc.).

- **1000 kHz** is the empirically determined sweet spot: load_image completes in ~70 seconds with a PRACC error rate low enough that retry logic can handle the occasional failure.
