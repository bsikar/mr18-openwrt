# Bug 20: Reset Supervisor IC

**Category**: Hardware/electrical
**Related docs**: [prerequisites](../guides/prerequisites.md), [address-map](../reference/address-map.md)

## Symptom

After investigating the pull-up issue ([Bug 19](bug-19-gpio-vs-pullup.md)), both GPIO drive directions were tested:

- `GPIO_SET` (drive HIGH): `GPIO_IN` reads bit 17 = 1
- `GPIO_CLR` (drive LOW): `GPIO_IN` reads bit 17 = 1

Neither driving HIGH nor driving LOW changed the pin state. It was stuck HIGH regardless of the GPIO output configuration.

## Root Cause

The MR18 board has a dedicated **reset supervisor IC** connected to the GPIO17 / reset button net. This IC is not a passive pull-up resistor -- it is an active CMOS output that drives the reset line HIGH with a strong driver (capable of 10-50 mA).

The reset supervisor's purpose is to hold the reset line in a known state during power-up and brown-out conditions. It actively drives the line HIGH with a low-impedance CMOS output stage. The AR9344 GPIO output driver cannot compete:

- The GPIO output driver can source/sink perhaps 2-4 mA
- The reset supervisor IC can source 10-50 mA with a CMOS push-pull output
- The supervisor's output impedance is much lower than the GPIO's

The GPIO output is effectively shorted against the supervisor's output. The supervisor always wins, keeping the pin HIGH regardless of what the GPIO is trying to do. This also means the JTAG GPIO approach is fundamentally broken on this board -- no amount of software configuration can override the reset supervisor.

## Fix

Abandon the JTAG GPIO approach entirely. Two alternative methods were identified:

1. **ESP-Prog EN pin**: The FT2232H's RTS line (on the UART connector) drives an NPN transistor connected to the MR18 reset button pad. This drives the reset line through the button's own pad, bypassing the reset supervisor (the button pad is on the downstream side of the supervisor).

2. **UART 'f' key**: Send the character `f` followed by newline over the serial console during the preinit prompt. This is OpenWrt's keyboard-based failsafe trigger, which does not require GPIO manipulation at all.

The final solution uses both: the UART thread watches for the preinit prompt and sends `f`, while the EN pin is asserted as a backup.

## Lesson

When a dedicated IC owns a signal line (reset supervisor, voltage regulator enable, power management IC), software GPIO control from the SoC cannot compete. The IC's output driver is designed to be authoritative on that net. To manipulate the signal, you must find a path that bypasses the IC -- such as the button pad itself, which is downstream of the supervisor.
