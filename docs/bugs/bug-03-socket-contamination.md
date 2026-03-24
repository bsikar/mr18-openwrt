# Bug 3: Socket Buffer Contamination

**Category**: JTAG/PRACC
**Related docs**: [script-reference](../reference/script-reference.md)

## Symptom

`load_image` reported completion in approximately 0.5 seconds for a 6.9 MB file. At the PRACC transfer rate of ~97 KB/s, this operation should take roughly 70 seconds. The command appeared to succeed, but the loaded data was incomplete or garbage.

## Root Cause

The `_drain()` method in the OpenOCD telnet wrapper had a hardcoded 0.5-second socket timeout:

```python
def _drain(self):
    self.sock.settimeout(0.5)
    try:
        while True:
            data = self.sock.recv(4096)
            ...
    except socket.timeout:
        break  # Assumed command was done
```

When `cmd("load_image ...")` was called, `_drain()` waited for 0.5 seconds of silence on the socket. Since `load_image` produces no output during the actual transfer (only a completion message at the end), the 0.5-second silence threshold was hit almost immediately. `_drain()` returned, and `cmd()` declared success -- even though `load_image` was still running in the background.

Subsequent commands sent to the same socket interleaved with `load_image`'s eventual completion message, corrupting the command/response stream.

## Fix

Thread the timeout from `cmd()` through to `_drain()`. For long-running commands like `load_image`, pass a timeout that exceeds the expected duration:

```python
def cmd(self, command, timeout=120):
    ...
    return self._drain(timeout=timeout)

def _drain(self, timeout=0.5):
    self.sock.settimeout(timeout)
    ...
```

Also remove the `except socket.timeout: break` pattern that silently swallowed the timeout as a success signal. Instead, raise an explicit error if the expected completion prompt is not received within the timeout window.

## Lesson

Hardcoded timeouts in generic I/O functions create invisible failures. When a drain/read function uses a fixed timeout to decide "the command is done," any command that takes longer than that timeout will appear to succeed instantly. The timeout must be a parameter, scaled to the expected duration of the specific operation.
