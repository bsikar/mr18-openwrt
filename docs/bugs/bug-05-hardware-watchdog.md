# Bug 5: Hardware Watchdog

**Category**: Boot/kernel
**Related docs**: [address-map](../reference/address-map.md)

## Symptom

The flash script completed cleanly: binary loaded, XOR checksum matched, kernel launched. The CPU was running (no longer halted). But the device booted into Cisco firmware instead of OpenWrt. `tcpdump` on the Ethernet link showed Meraki management frames (ethertype `0x0642`) -- the signature of Cisco's cloud management protocol.

## Root Cause

The AR9344 has a hardware watchdog timer that runs independently of the CPU. It is clocked by the APB bus clock and counts down regardless of whether the CPU is halted, running, or in debug mode.

The sequence:

1. MR18 powers on. Nandloader starts and **enables the hardware watchdog** with a timeout of approximately 90 seconds.
2. JTAG halts the CPU at ~2 seconds.
3. Binary load takes ~70 seconds. During this time the CPU is halted, but the watchdog keeps counting.
4. The watchdog timer expires while the CPU is still halted (or shortly after).
5. The watchdog triggers a full SoC reset.
6. The SoC reboots from NAND, running the Cisco firmware normally.

The result is that our carefully loaded OpenWrt binary in RAM is wiped by the reset, and the device boots Cisco as if nothing happened.

## Fix

An initial attempt to disable the watchdog via a direct MMIO write to `WDOG_CTRL` at `0xB8060008` failed—the write broke the PRACC state machine (possibly because the watchdog control register has side effects during EJTAG access).

The bug ultimately self-resolved: PRACC activity during `load_image` and verification generates enough bus transactions to keep the watchdog alive. Each PRACC memory access involves the CPU (even while halted in debug mode) executing micro-operations that touch the bus, which the watchdog interprets as activity. As long as the JTAG transfer is actively running, the watchdog does not expire.

The critical window is any long pause after halting the CPU but before starting `load_image`. The script avoids this by starting the binary load immediately after a successful halt.

## Lesson

Hardware timers (watchdogs, PMU timers, RTC) run independently of the CPU halt state. JTAG halting the CPU does not halt the SoC's peripheral clocks. When working with EJTAG on devices that enable hardware watchdogs during early boot, either disable the watchdog explicitly (if the register is safely writable) or ensure continuous PRACC activity to keep it fed.
