# Bug 6: Named Pipe EOF

**Category**: Toolchain
**Related docs**: [script-reference](../reference/script-reference.md)

## Symptom

The script attempted 6 power-cycle retries, but the device never actually cycled. On every attempt, the CPU's program counter (PC) was consistently at `0xa0200030` -- the address of an `SDBBP` (Software Debug Breakpoint) instruction left over from a previous debugging session's trampoline. The device was never power-cycled between attempts; it was still sitting at the old halt point.

## Root Cause

The `psu()` function opened the scpi-repl named pipe (FIFO) in `"w"` (write) mode:

```python
def psu(command):
    with open(PSU_PIPE, "w") as f:
        f.write(command + "\n")
```

When a file opened with `"w"` mode on a FIFO is closed (at the end of the `with` block), the kernel sends an EOF to the reading end. `scpi-repl` reads from the FIFO in a loop; when it receives EOF, it exits the read loop and stops accepting commands.

The first `psu("OFF")` call worked—it opened the FIFO, wrote the command, and closed it (sending EOF). `scpi-repl` processed "OFF" and then exited its read loop. All subsequent `psu()` calls opened the FIFO in write mode, but with no reader on the other end, the `open()` call blocked forever (FIFO semantics: a writer blocks until a reader opens the other end).

The net result: the first PSU command executed, but all subsequent commands hung silently.

## Fix

Open the FIFO in append mode (`"a"`, which uses `O_APPEND`):

```python
def psu(command):
    with open(PSU_PIPE, "a") as f:
        f.write(command + "\n")
```

With `O_APPEND`, closing the file descriptor does not send EOF to the reader. `scpi-repl` stays in its read loop, and subsequent opens succeed because the reader is still present.

The bug was diagnosed by examining the PC value: `0xa0200030` pointed to a known trampoline `SDBBP` from a previous session, proving the device had never been power-cycled (if it had, the PC would have been at the Nandloader entry point).

## Lesson

Named pipe (FIFO) write mode (`"w"`) sends EOF on close, which terminates the reader's read loop. Use append mode (`"a"`) when the reader is a persistent process that should survive multiple writer open/close cycles. This is a subtle POSIX FIFO semantic that is easy to overlook.
