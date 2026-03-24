# Bug 16: Failsafe Timing

**Category**: Failsafe
**Related docs**: [script-reference](../reference/script-reference.md), [address-map](../reference/address-map.md)

## Symptom

GPIO17 (the reset button input) was asserted LOW at t=30 seconds after kernel launch. No failsafe mode was triggered. The device booted normally with the Meraki NAND overlay.

## Root Cause

Two problems combined:

**1. Failsafe window is narrow and earlier than expected.** OpenWrt's failsafe detection happens during preinit, which runs a specific check for GPIO button state or a magic packet. The failsafe detection window on the MR18 is approximately t=10s to t=18s after the lzma-loader starts. By t=30s, preinit has finished, the overlay is mounted, and normal init is running. The failsafe check is no longer active.

**2. gpio-keys driver steals GPIO17 direction.** The Linux `gpio-keys` driver is configured in the device tree for the MR18 reset button. When the driver loads, it reconfigures GPIO17 as an input under its own control. Any previous GPIO output-enable configuration set via JTAG MMIO writes is overwritten by the driver. This means that even if the timing were correct, JTAG-based GPIO manipulation would be fighting the kernel's own driver for control of the pin direction.

## Fix

Hammer GPIO17 from t=0 (immediately after kernel launch), repeating every 1.5 seconds for 25 seconds. This blankets the entire possible failsafe window rather than trying to hit a precise moment:

```python
for i in range(17):
    # Assert GPIO17 LOW (button pressed)
    ocd.cmd("mww 0xB8040000 0x00020000")  # GPIO_OE: enable output
    ocd.cmd("mww 0xB8040010 0x00020000")  # GPIO_CLR: drive LOW
    time.sleep(1.5)
```

This approach was later abandoned in favor of the EN pin method (see [Bug 19](bug-19-gpio-vs-pullup.md) through [Bug 23](bug-23-en-before-boot.md)) because JTAG GPIO manipulation has fundamental limitations (Bugs 17-20).

## Lesson

Preinit failsafe timing must be measured, not estimated. The window is typically much earlier and shorter than intuition suggests, because the LZMA decompression time (which precedes preinit) is not visible and cannot be estimated without measurement.
