# Bug 18: mdw/mww Require Halt

**Category**: JTAG/PRACC
**Related docs**: [address-map](../reference/address-map.md)

## Symptom

After adding `resume` to fix [Bug 17](bug-17-hammer-freezes-cpu.md), the GPIO hammer loop ran with resume/sleep cycles. But reading `GPIO_IN` (`0xB8040004`) via `mdw` showed bit 17 in an indeterminate state every cycle. The GPIO writes appeared to have no effect.

## Root Cause

The fix for Bug 17 added `resume` after the GPIO writes but before the `mdw` read used to check `GPIO_IN`. The sequence was:

```python
ocd.cmd("mww 0xB8040000 0x00020000")  # GPIO_OE (this halts CPU implicitly)
ocd.cmd("mww 0xB8040010 0x00020000")  # GPIO_CLR
ocd.cmd("resume")                      # CPU now RUNNING
result = ocd.cmd("mdw 0xB8040004")     # GPIO_IN -- but CPU is RUNNING
```

After `resume`, the target state is `RUNNING`. When `mdw` is issued against a running target, OpenOCD returns `ERROR_TARGET_NOT_HALTED`. However, the Python telnet wrapper did not check for this error -- it treated any response as valid data, producing meaningless readings.

The GPIO writes (via `mww`) also require halt state. The implicit halt behavior from Bug 17 only works for the first command in a sequence -- once `resume` is called, subsequent `mww`/`mdw` commands fail silently.

## Fix

Add an explicit `halt` + `wait_halt` at the **start** of each hammer cycle, before any memory operations:

```python
for i in range(17):
    ocd.cmd("halt")
    ocd.cmd("wait_halt")
    ocd.cmd("mww 0xB8040000 0x00020000")  # GPIO_OE
    ocd.cmd("mww 0xB8040010 0x00020000")  # GPIO_CLR
    result = ocd.cmd("mdw 0xB8040004")    # GPIO_IN (now valid)
    ocd.cmd("resume")
    time.sleep(1.5)
```

The sequence is now: halt (CPU stopped) -> write GPIO -> read GPIO -> resume (CPU runs) -> sleep (kernel progresses).

## Lesson

OpenOCD MIPS EJTAG requires the CPU to be explicitly halted before any `mdw`/`mww` memory access. After a `resume`, the target is in `RUNNING` state and memory operations will fail. Do not rely on implicit halt behavior -- make the halt/resume state transitions explicit in every JTAG operation cycle.
