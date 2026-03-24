# Bug 21: Manual Prompt Too Late

**Category**: Failsafe
**Related docs**: [script-reference](../reference/script-reference.md)

## Symptom

The script printed "MANUAL ACTION REQUIRED—press the reset button now" at t=25 seconds after kernel launch. By this time, the failsafe detection window (t=10-18s) had already closed. Pressing the button had no effect.

## Root Cause

The code structure was:

```python
# GPIO hammer loop (from Bugs 16-20, now known to be useless)
for i in range(17):
    ocd.cmd("halt")
    ocd.cmd("mww ...")   # GPIO writes
    ocd.cmd("resume")
    time.sleep(1.5)      # 17 * 1.5s = 25.5s total

# Only now does the prompt appear
print("MANUAL ACTION REQUIRED -- press the reset button now")
```

The JTAG GPIO hammer loop consumed 25.5 seconds before printing the manual action prompt. But the GPIO hammer was ineffective (Bugs 19-20: pull-ups and reset supervisor prevent GPIO control of the reset line). So the script spent 25 seconds doing nothing useful, then asked the user to act—after the window had already closed.

The dead code did not just fail silently; it actively consumed the time window needed for the user to act.

## Fix

Remove the JTAG GPIO hammer entirely. Replace it with the EN pin assertion, which fires immediately after kernel launch:

```python
# Assert EN immediately (no delay)
ser.rts = True    # EN LOW (button pressed)
time.sleep(40)    # Hold through entire possible window
ser.rts = False   # Release
```

The EN pin approach does not require any JTAG commands, so it does not halt the CPU and does not consume time with useless operations.

## Lesson

Dead code that consumes time is worse than no code at all. The GPIO hammer was kept in the script after it was known to be ineffective, "just in case." Its 25-second runtime prevented any alternative approach (manual button press, EN pin) from acting within the failsafe window. When code is proven ineffective, remove it rather than leaving it as a time-consuming no-op.
