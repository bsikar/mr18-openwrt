# Bug 23: EN Before Boot

**Category**: Failsafe
**Related docs**: [script-reference](../reference/script-reference.md)

## Symptom

The EN pin was asserted (RTS = True, NPN pulls GPIO17 LOW) at t=12 seconds after the lzma-loader was launched. EN was held for 5 seconds and released at t=17 seconds. Failsafe mode was not triggered.

## Root Cause

The timing assumption was wrong. The lzma-loader takes approximately 13 seconds to decompress the kernel. The kernel then takes several more seconds to initialize before reaching the preinit phase where failsafe detection occurs.

The actual timeline:

| Time | Event |
|------|-------|
| t=0 | lzma-loader launched (CPU resumes at trampoline) |
| t=0-13s | LZMA decompression in progress |
| t=13s | Kernel entry point reached |
| t=13-18s | Kernel init (drivers, subsystems) |
| t=18-25s | Preinit runs, **failsafe window open** |
| t=25s+ | Normal init proceeds, window closed |

The EN assertion at t=12s hit during LZMA decompression -- the kernel had not even started yet. By t=17s (when EN was released), preinit still had not run. The GPIO17 state during decompression is irrelevant because no software is checking it.

## Fix

Assert EN from t=2s (shortly after kernel launch) and hold for 40 seconds:

```python
FAILSAFE_EN_DELAY = 2.0   # seconds after launch before asserting EN
FAILSAFE_EN_HOLD  = 40.0  # seconds to hold EN LOW

time.sleep(FAILSAFE_EN_DELAY)
ser.rts = True             # Assert EN (GPIO17 LOW = button pressed)
time.sleep(FAILSAFE_EN_HOLD)
ser.rts = False            # Release
```

The 2-second delay avoids asserting during the very early lzma-loader startup (where a reset might cause problems). The 40-second hold blankets the entire range from LZMA decompression through kernel init through preinit and well beyond. No matter when the failsafe window actually opens, EN is already asserted.

Additionally, the UART thread watches for the preinit prompt (`Press the [f] key and hit [enter] to enter failsafe mode`) and sends `f\n` as a belt-and-suspenders backup. Both the EN pin (hardware) and the UART 'f' key (software) independently trigger failsafe.

## Lesson

Do not guess timing. When the target window's position is uncertain (because it depends on variable-length operations like LZMA decompression), blanket the window generously. Assert early, hold long, and release late. The cost of holding too long (a few extra seconds of delay) is negligible compared to the cost of missing the window (complete failure, requiring a retry from scratch).
