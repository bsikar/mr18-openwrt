# Bug 22: Resistor Wrong Side

**Category**: Hardware/electrical
**Related docs**: [prerequisites](../guides/prerequisites.md)

## Symptom

The EN pin assertion fired at the expected time (RTS line toggled, NPN transistor activated). But failsafe mode was never triggered. The device booted with the Meraki NAND overlay as before.

## Root Cause

A 100 ohm series resistor was installed between the GPIO17 net on the MR18 and the EN transistor on the ESP-Prog. The resistor was intended to limit current in case the reset supervisor IC and the NPN transistor both tried to drive the line simultaneously (contention protection).

However, the resistor was on the **wrong side** of the signal path. The circuit was:

```
ESP-Prog NPN collector --[100R]-- GPIO17 net -- Reset supervisor IC
```

When the NPN transistor turned on (pulling its collector to GND), the current path was:

- Reset supervisor drives GPIO17 HIGH at 3.3 V
- Current flows: supervisor -> GPIO17 net -> 100R -> NPN collector -> GND
- Voltage at GPIO17 net = 3.3 V - (I * 0) = 3.3 V (no drop on supervisor side)
- Voltage at NPN collector = 0 V (NPN saturated)
- Voltage drop is across the 100 ohm resistor, but it is between the NPN and the GPIO17 net

The GPIO17 net stays at 3.3 V because the voltage drop occurs between GPIO17 and the NPN, not between the supervisor and GPIO17. The supervisor's output is still driving the GPIO17 net directly, and the resistor only limits how much current the NPN sinks—it does not pull the GPIO17 voltage down.

## Fix

Remove the 100 ohm resistor. Wire the EN pin (NPN collector) directly to the reset button pad on the MR18:

```
ESP-Prog NPN collector -- Reset button pad (GPIO17 side)
```

The reset button pad is the point where a physical button press would pull the line to GND. Connecting the NPN collector directly here allows the NPN to pull the line LOW with the same effect as a physical button press.

The 100 ohm resistor is not needed because the ESP-Prog's NPN transistor circuit already has a base resistor that limits the NPN's saturation current. The collector can safely pull the reset line LOW without additional series resistance.

## Lesson

Series resistors for contention protection must be on the correct side of the signal path. When two drivers fight over a signal, the resistor must be between the driver you want to limit and the signal net. Placing it between the signal net and the other driver limits the wrong current path and leaves the signal voltage unchanged.
