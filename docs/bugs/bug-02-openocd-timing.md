# Bug 2: OpenOCD Startup Timing

**Category**: JTAG/PRACC
**Related docs**: [script-reference](../reference/script-reference.md), [prerequisites](../guides/prerequisites.md)

## Symptom

OpenOCD logged:

```
JTAG scan chain interrogation failed: all ones
```

```
TDO seems stuck high
```

The `halt` command never succeeded. OpenOCD could not detect any device on the JTAG chain.

## Root Cause

OpenOCD was started **before** the MR18 was powered on. At init time, OpenOCD performs a JTAG scan chain interrogation—it clocks patterns through TDI/TDO to discover TAPs. With no power applied to the MR18, there is no live TAP. TDO is pulled high by the idle state (or the TMS pull-up resistor), so every bit read back is `1`.

The correct timing window is narrow:

- **Too early** (before power-on): No TAP. Scan chain interrogation fails.
- **Correct** (~1.5 s after power-on): Nandloader is running. The EJTAG TAP is alive and responding. CPU can be halted.
- **Too late** (>2 s after power-on): Nandloader has disabled JTAG probing. TDO stops responding to scan requests.

## Fix

Start OpenOCD approximately 1.5 seconds after power-on. At this point the Nandloader is executing (TAP is alive) but has not yet disabled JTAG. The `mr18_flash.py` script automates this timing via the PSU power-cycle sequence:

1. Power off (PSU output disabled)
2. Power on (PSU output enabled)
3. Wait ~1.5 s
4. Launch OpenOCD
5. Connect via telnet
6. Issue `halt`

If the halt fails (Nandloader already disabled JTAG), the script retries by power-cycling again, up to `MAX_ATTEMPTS` (default 6) times.

## Lesson

The JTAG TAP must be live and responsive when OpenOCD performs its initial scan chain interrogation. The intuitive approach of "get OpenOCD ready, then power on the device" is wrong—OpenOCD's init happens at startup, not on demand. Power must be on and the TAP must be active before OpenOCD starts.
