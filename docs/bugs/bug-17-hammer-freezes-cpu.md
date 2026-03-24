# Bug 17: Missing Resume

**Category**: JTAG/PRACC
**Related docs**: [address-map](../reference/address-map.md), [script-reference](../reference/script-reference.md)

## Symptom

The GPIO17 hammer loop ran all 17 cycles (each with `mww` to set GPIO_OE and GPIO_CLR, then a 1.5-second sleep). After the full 25-second hammer sequence, `192.168.1.1` never responded. The device appeared completely dead—no Ethernet link, no Meraki frames, nothing.

## Root Cause

On MIPS EJTAG targets, OpenOCD's `mdw` (memory display word) and `mww` (memory write word) commands use the PRACC mechanism, which requires the CPU to be in debug halt state. When the CPU is running and an `mdw` or `mww` command is issued, OpenOCD **implicitly halts the CPU** to perform the PRACC operation.

The kernel was launched with `resume` and was actively running (booting Linux). The first `mww` in the hammer loop halted the CPU to perform the GPIO register write. The script then called `time.sleep(1.5)` -- but the CPU was still halted. The next `mww` also required halt, which was already the case. This continued for all 17 iterations.

The CPU was halted from the very first GPIO write and never resumed. The kernel never progressed past whatever instruction it was executing when the first `mww` arrived. No boot happened, no preinit ran, no failsafe window ever opened.

## Fix

Call `resume` after the GPIO writes and before the `sleep`, so the kernel continues executing between hammer cycles:

```python
for i in range(17):
    ocd.cmd("halt")
    ocd.cmd("mww 0xB8040000 0x00020000")  # GPIO_OE
    ocd.cmd("mww 0xB8040010 0x00020000")  # GPIO_CLR
    ocd.cmd("resume")                      # Let kernel run
    time.sleep(1.5)
```

This allows the kernel to make progress (boot, load drivers, reach preinit) between each GPIO assertion.

## Lesson

MIPS EJTAG `mdw` and `mww` commands implicitly halt the CPU. Any code that interleaves JTAG memory operations with kernel execution must explicitly `resume` the CPU after each set of JTAG operations. Forgetting to resume turns "periodic GPIO toggling" into "permanent CPU halt."
