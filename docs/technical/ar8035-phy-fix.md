# AR8035 PHY RGMII RX Clock Delay Fix

How a missing 2 ns internal delay line in the AR8035 Ethernet PHY causes total RX failure, and the bare-metal MIPS binary that fixes it by writing a single debug register bit over MDIO.

## The Symptom

After OpenWrt boots on the MR18:

- **TX works:** ARP requests are transmitted, visible on a host connected directly to the MR18's Ethernet port. `tx_packets` increments normally.
- **RX is completely broken:** `rx_packets` stays at zero. Every incoming frame triggers FCS (Frame Check Sequence) errors. The host sees ARP replies never arrive. `ping` fails in both directions despite the link being physically up at 1 Gbps.

## Root Cause: RGMII RX Clock Timing Skew

The QCA9557 SoC connects to the AR8035 PHY over an RGMII (Reduced Gigabit Media Independent Interface) bus. RGMII clocks data on both rising and falling edges of a 125 MHz clock at 1 Gbps. The specification requires a 2 ns delay between the clock and data signals so the MAC can sample data at the correct point.

The AR8035 has an internal programmable delay line for this purpose. On the RX path (PHY-to-MAC direction), this delay is controlled by **bit 15 of debug register 0x00**. When this bit is cleared (the default), the delay line is disabled and the RX clock arrives aligned with the data edges—the MAC samples at transition points, producing garbage.

### The DTS Gap

The OpenWrt device tree for the MR18 specifies:

```dts
phy-mode = "rgmii";
```

The correct value should be `"rgmii-rxid"`, which tells the `at803x` PHY driver to enable the RX internal delay. With plain `"rgmii"`, the driver leaves the delay configuration at its hardware default (disabled), and the RX path is broken.

## The Fix: Debug Register 0x00 Bit 15

Set bit 15 in the AR8035's debug register 0x00 to enable the RGMII RX clock delay line.

### Debug Register Access via MDIO

AR8035 debug registers are not directly addressable through standard MDIO register space. Access requires a two-step protocol using standard MDIO registers 0x1D and 0x1E:

| Step | Operation | MDIO Register | Purpose |
|------|-----------|---------------|---------|
| 1 | Write | 0x1D | Set the debug register address |
| 2 | Read/Write | 0x1E | Access the debug register data |

To read debug register N:
```
mdio_write(0x1D, N)     # select debug register
value = mdio_read(0x1E) # read its value
```

To write debug register N:
```
mdio_write(0x1D, N)     # select debug register
mdio_write(0x1E, value) # write new value
```

### Register Operations

The fix applies two debug register modifications:

**1. Hibernation disable (debug 0x0B, bit 15)** -- Clears the `PS_HIB_EN` bit to prevent the PHY from entering a low-power hibernation state. In practice, this turned out to be a **no-op** on the MR18 (the bit was already clear, and hibernation was not interfering), but it is included as a defensive measure.

```
dbg_mask(0x0B, 0x8000, 0)   # clear bit 15
```

**2. RX delay enable (debug 0x00, bit 15)** -- This is the **actual fix.** Sets the bit that enables the internal RGMII RX clock delay line (~2 ns).

```
dbg_mask(0x00, 0, 0x8000)   # set bit 15
```

### PHY ID Verification

Before writing any registers, the binary reads MDIO registers 0x02 and 0x03 to verify the PHY identity:

| Register | Expected Value | Contents |
|----------|---------------|----------|
| 0x02 (PHY ID 1) | `0x004d` | Qualcomm/Atheros OUI upper bits |
| 0x03 (PHY ID 2) | `0xd072` | AR8035 model + revision |

If the PHY ID does not match, the binary aborts without writing any registers.

## ar8035.c Walkthrough

The fix is implemented as a standalone C program (`ar8035-fix/ar8035.c`) that uses raw Linux syscalls—no libc, no dynamic linking.

### Raw Syscall Wrappers

Four syscall wrappers using inline MIPS assembly:

| Function | Syscall # | Purpose |
|----------|-----------|---------|
| `sys_write(fd, buf, len)` | 4004 | Console output to stdout |
| `sys_socket(domain, type, proto)` | 4183 | Create `AF_INET`/`SOCK_DGRAM` socket for ioctl |
| `sys_ioctl(fd, req, arg)` | 4054 | MDIO register access via `SIOCGMIIREG`/`SIOCSMIIREG` |
| `sys_exit(code)` | 4001 | Process exit |

### MIPS O32 ABI: Error Handling and Clobbers

The MIPS O32 Linux syscall ABI has two critical properties that differ from x86:

**Error signaling via `$a3`:** On error, `$a3` is set to 1 and `$v0` contains a positive errno value. On success, `$a3` is 0 and `$v0` contains the return value. A naive check of `v0 < 0` (as on x86) would miss errors entirely since errno values are positive.

```c
return err ? -r : r;  // err=$a3, r=$v0
```

**Register clobbers (`SYSCALL_CLOBBERS` macro):** The kernel does NOT guarantee that `$t0`-`$t9` (`$8`-`$15`, `$24`-`$25`) are preserved across syscalls. glibc declares these as clobbers in its syscall wrappers. Without this macro, GCC may keep live values in temporary registers across a syscall, producing silent data corruption.

```c
#define SYSCALL_CLOBBERS \
    "v1", "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7", "t8", "t9", \
    "memory"
```

### MDIO Access Layers

The MDIO access is layered through four functions:

```
ar8035_main()
    --> dbg_mask(reg, clear_bits, set_bits)   // read-modify-write debug register
        --> dbg_read(reg) / dbg_write(reg, val)   // two-step debug access
            --> mdio_read(reg) / mdio_write(reg, val)   // standard MDIO via ioctl
                --> mdio_ioctl(req, ifr)   // raw ioctl wrapper
                    --> sys_ioctl(sock, req, arg)   // syscall
```

Standard MDIO ioctl commands used:

| ioctl | Value | Purpose |
|-------|-------|---------|
| `SIOCGMIIPHY` | `0x8947` | Query PHY address from driver |
| `SIOCGMIIREG` | `0x8948` | Read MDIO register |
| `SIOCSMIIREG` | `0x8949` | Write MDIO register |

## ar8035_start.S Walkthrough

The assembly entry point (`ar8035-fix/ar8035_start.S`) is a minimal `_start` that:

1. **Sets up `$gp`** (global pointer) for accessing global/static variables:
   ```asm
   lui     $gp, %hi(_gp)
   addiu   $gp, $gp, %lo(_gp)
   ```

2. **Aligns the stack** to 8 bytes (O32 ABI requirement):
   ```asm
   li      $t0, ~7
   and     $sp, $sp, $t0
   ```

3. **Calls C entry** via `jalr` (not `jal`, since we use a register-indirect call):
   ```asm
   la      $t9, ar8035_main
   jalr    $t9
   nop
   ```

4. **Exits** with the return value from `ar8035_main` as the exit code:
   ```asm
   move    $a0, $v0
   li      $v0, 4001      # __NR_exit
   syscall
   ```

## Compilation

```makefile
CFLAGS  = -O2 -msoft-float -mno-abicalls -fno-pic
LDFLAGS = -nostdlib -nostartfiles -Wl,-z,noexecstack -Wl,-e,_start -static
```

### Why each flag is needed

| Flag | Reason |
|------|--------|
| `-msoft-float` | The AR9344 / QCA9557 has **no hardware FPU**. Any floating-point instruction causes an "Illegal instruction" trap. GCC must emit soft-float library calls for any FP operation. |
| `-nostdlib` | glibc's `crt0.o` startup code uses FPU instructions (e.g., to save/restore FP registers during `__libc_start_main`). Linking against glibc causes "Illegal instruction" on the first FP instruction in crt0, before `main()` even runs. |
| `-nostartfiles` | Prevents linking glibc's `crt1.o`/`crti.o`/`crtn.o`. We provide our own `_start` in `ar8035_start.S`. |
| `-mno-abicalls` | Disables MIPS position-independent code generation. Without this, GCC emits GOT (Global Offset Table) references and `.cpload`/`.cprestore` sequences that require dynamic linker support. |
| `-fno-pic` | Companion to `-mno-abicalls`. Ensures no PIC/PLT (Procedure Linkage Table) indirection is generated. The binary is fully static with direct addresses. |
| `-static` | Produces a static ELF binary with no dynamic library dependencies. |
| `-Wl,-e,_start` | Sets the ELF entry point to `_start` (our assembly entry, not glibc's). |

### Output

The resulting `ar8035-fix` binary is **5,592 bytes** -- a fully static MIPS32 big-endian ELF executable. Small enough to transfer over UART in seconds.

### Cross-compilation

The build requires a MIPS cross-compiler. On Debian/Ubuntu:
```sh
apt install gcc-mips-linux-gnu binutils-mips-linux-gnu
```

On other systems, a Docker-based build is provided:
```sh
make docker
```

## Deployment

### Initial application via send_binary.py

`ar8035-fix/send_binary.py` transfers the compiled binary to the MR18 over UART using hex encoding (see [uart-transfer.md](uart-transfer.md)), makes it executable, and runs it. The fix takes effect immediately -- `rx_packets` begins incrementing and bidirectional Ethernet communication is restored.

### Persistence via hotplug script

The fix must be reapplied after every reboot because the AR8035 PHY resets to its default register values on power cycle. A hotplug script at `/etc/hotplug.d/iface/10-ar8035-fix` fires on `ifup` for the `lan` interface:

```sh
#!/bin/sh
[ "$ACTION" = "ifup" ] && [ "$INTERFACE" = "lan" ] && /usr/bin/ar8035-fix
```

This runs the fix binary every time the LAN interface comes up, ensuring RGMII RX delay is enabled before any network traffic flows.

### Proper upstream fix

The correct long-term fix is to change the device tree source:

```dts
# Before (broken):
phy-mode = "rgmii";

# After (correct):
phy-mode = "rgmii-rxid";
```

This tells the `at803x` kernel driver to enable the RX internal delay during PHY initialization, eliminating the need for the userspace binary entirely.

## Cross-references

- [UART Transfer Protocol](uart-transfer.md) -- hex-over-UART protocol used by `send_binary.py`
- [Script Reference](../reference/script-reference.md) -- CLI usage for `send_binary.py`
- [Project Overview](../overview.md) -- hardware summary including AR8035 PHY
