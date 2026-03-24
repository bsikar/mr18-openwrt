# Bug 19: GPIO vs Pull-up

**Category**: Hardware/electrical
**Related docs**: [address-map](../reference/address-map.md), [prerequisites](../guides/prerequisites.md)

## Symptom

With the halt/resume cycle correct (Bug 17 and Bug 18 fixed), GPIO17 was configured as output (`GPIO_OE` bit 17 set) and driven LOW (`GPIO_CLR` bit 17 written). Reading `GPIO_IN` showed bit 17 = 1 (HIGH). The GPIO was being driven LOW, but the physical pin was reading HIGH.

## Root Cause

GPIO17 is the reset button input on the MR18. The board has pull-up resistors on this signal to ensure it reads HIGH (not pressed) by default. Additionally, the JTAG adapter's nSRST line may contribute a pull-up on the same net.

The effective pull-up resistance is approximately 5 kohm (board pull-up in parallel with any JTAG pull-up). The AR9344 GPIO output driver has limited drive strength -- its internal PMOS/NMOS transistors can source/sink only a few milliamps.

The pull-up current at 3.3 V through 5 kohm is:

```
I = V / R = 3.3 / 5000 = 0.66 mA
```

While 0.66 mA seems small, the AR9344 GPIO output may not be able to sink enough current to pull the voltage below the logic LOW threshold (typically ~0.8 V for 3.3 V LVCMOS). The pin voltage settles at some intermediate value that the input buffer still reads as HIGH.

This is a fundamental electrical limitation: the SoC's GPIO output driver cannot overcome the external pull-up.

## Fix

The JTAG GPIO approach was proposed to be abandoned in favor of physically pressing the reset button. This was ultimately superseded by the EN pin approach using the ESP-Prog's UART connector (see [Bug 20](bug-20-reset-supervisor.md) and subsequent bugs for the evolution of this approach).

## Lesson

External pull-up resistors can overpower SoC GPIO output drivers. When a GPIO pin has board-level pull-ups designed for a specific purpose (button debouncing, default state), driving that pin from software may not produce the expected voltage level. Always check the board's pull-up/pull-down network before attempting to drive a GPIO from the SoC.
