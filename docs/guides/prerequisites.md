# Prerequisites

Everything needed before running the flash scripts: hardware, software, firmware images, and host network configuration.

## Hardware

### Bill of Materials

| Item | Purpose | Notes |
|------|---------|-------|
| Cisco Meraki MR18 | Target device | Any hardware revision; PCB has unpopulated 10-pin JTAG header |
| ESP-Prog (FT2232H) | JTAG adapter + UART console | Channel 0 = JTAG, Channel 1 = UART (console + EN control) |
| 10-pin 2.54 mm header | JTAG connection | Solder to the MR18's unpopulated J3 JTAG pads |
| Soldering iron + solder | Header installation | Fine-tip iron recommended for the dense pad spacing |
| 4.7 kohm resistor | TMS pull-up | Solder between TMS and VCC on the JTAG header; prevents TMS floating during power transitions |
| Hookup wire | Connections | For EN wire (ESP-Prog UART connector EN pin to MR18 reset button non-GND pad) |
| Bench power supply (SCPI) | Automated power cycling | 12 V / 1.5 A capable; must support SCPI over USB/serial for `scpi-repl`. A manual PSU works but requires hand-toggling power during the halt timing window |
| Ethernet cable | Host-to-MR18 link | Direct connection, no switch needed |
| Host PC | Running the scripts | Linux (tested on Arch); USB ports for ESP-Prog + PSU + Ethernet adapter |

### Wiring

See [Hardware Setup](hardware-setup.md) for full connector pinouts, pin-to-pin wiring tables with wire colors, and Mermaid diagrams.

## Software

### OpenOCD

OpenOCD provides the JTAG transport between the host and the MR18's EJTAG TAP.

| Distribution | Install Command |
|-------------|----------------|
| Arch Linux  | `sudo pacman -S openocd` |
| Debian / Ubuntu | `sudo apt install openocd` |
| Fedora      | `sudo dnf install openocd` |

Verify the installation:

```sh
openocd --version
# Should print OpenOCD 0.12.x or later
```

### Python 3

All scripts require Python 3.8+. The following packages are needed:

```sh
pip install pyserial
```

Additional Python dependencies by script:

| Package | Required By | Purpose |
|---------|------------|---------|
| `pyserial` | `mr18_flash.py`, `send_binary.py`, `uart_transfer.py` | Serial port access for ESP-Prog UART |
| `capstone` | `verify_asm.py` only | MIPS disassembler for instruction encoding verification |

```sh
# Only needed if you want to run verify_asm.py
pip install capstone
```

### scpi-repl

The `mr18_flash.py` script controls the bench PSU through [scpi-repl](https://github.com/T-O-M-Tool-Oauto-Mationator/scpi-instrument-toolkit), which provides a persistent SCPI command interface via a named pipe.

Install it according to its repository instructions. The flash script expects the `scpi-repl` binary to be on `$PATH`.

If you are using a manual (non-SCPI) PSU, you will need to toggle power by hand at the script's prompts. Set `PSU_PIPE` to `/dev/null` and handle power cycling manually.

### Cross-Compiler (ar8035-fix only)

Building the `ar8035-fix` binary requires a MIPS big-endian cross-compiler:

| Distribution | Install Command |
|-------------|----------------|
| Debian / Ubuntu | `sudo apt install gcc-mips-linux-gnu binutils-mips-linux-gnu` |
| Arch Linux  | Use Docker (see below) |

**Docker alternative** (works on any distro):

```sh
cd ar8035-fix/
make docker
```

This runs:

```sh
docker run --rm -v $(pwd):/work -w /work debian:bookworm-slim \
  sh -c "apt-get update -q && apt-get install -y -q gcc-mips-linux-gnu make file && make"
```

A pre-built `ar8035-fix` binary (5592 bytes, MIPS32 big-endian static ELF) is included in the repository, so building from source is only needed if you modify the C or assembly code.

### Other Tools

The following standard utilities are used by `mr18_flash.py` and should already be present on most Linux systems:

- `nmap` -- ARP-level host detection during `wait_for_openwrt`
- `ping` -- ICMP reachability check
- `nc` (netcat) -- sysupgrade image transfer via TCP
- `scp` / `ssh` -- alternative sysupgrade transfer path
- `ip` -- host NIC configuration
- `pkill` / `tail` -- process management for scpi-repl

## Firmware Downloads

Both images are from the [OpenWrt 25.12.0 release](https://downloads.openwrt.org/releases/25.12.0/targets/ath79/nand/):

```sh
cd firmware/

wget https://downloads.openwrt.org/releases/25.12.0/targets/ath79/nand/openwrt-25.12.0-ath79-nand-meraki_mr18-initramfs-kernel.bin
wget https://downloads.openwrt.org/releases/25.12.0/targets/ath79/nand/openwrt-25.12.0-ath79-nand-meraki_mr18-squashfs-sysupgrade.bin
```

### Verify Integrity

```sh
echo "db191ecf0224f030365d604aa3919da9  openwrt-25.12.0-ath79-nand-meraki_mr18-initramfs-kernel.bin" | md5sum -c
echo "53e272bed2041616068c6958fe28a197  openwrt-25.12.0-ath79-nand-meraki_mr18-squashfs-sysupgrade.bin" | md5sum -c
# Both should print: OK
```

| Image | Size | MD5 |
|-------|------|-----|
| Initramfs kernel | 6,931,053 bytes | `db191ecf0224f030365d604aa3919da9` |
| Sysupgrade | - | `53e272bed2041616068c6958fe28a197` |

The initramfs kernel is loaded into RAM via JTAG and booted directly. The sysupgrade image is transferred to the running initramfs system (via telnet/nc, SSH/SCP, or UART hex encoding) and flashed to NAND with `sysupgrade -n`.

## Host Network Configuration

The MR18 in OpenWrt failsafe mode comes up at `192.168.1.1` with a static IP. The host NIC must be on the same subnet.

### Finding your NIC name

Plug in your USB Ethernet adapter and run:

```sh
ip link show | grep -E '<.*>(.*state)' | grep -v -E 'veth|lo:|docker|cni|flannel|tailscale|wlp'
```

Look for the physical Ethernet adapter. It will have one of these naming styles:

| Name format | Example | Meaning |
|-------------|---------|---------|
| `ethN` | `eth0` | Classic kernel naming (assigned in order of detection) |
| `enxMACADDR` | `enx6c1ff71fee83` | Predictable naming by MAC address |
| `enpXsYfZ...` | `enp0s13f0u1u4u2c2` | Predictable naming by PCI/USB path |

All three can refer to the same physical adapter—the kernel assigns one primary name and lists the others as aliases. Check with:

```sh
ip link show eth0
# Output includes "altname enx6c1ff71fee83" etc.
```

If the cable is not plugged in, the interface shows `NO-CARRIER` and `state DOWN`. This is normal before wiring up the MR18.

### Configuring the script

Edit `HOST_NIC` at the top of `jtag/mr18_flash.py` to match your adapter's primary name:

```
HOST_NIC  = "eth0"              # use whatever name your system assigns
HOST_IP   = "192.168.1.2/24"
```

### Manual configuration (if not using the script)

```sh
sudo ip addr flush dev <your-nic>
sudo ip addr add 192.168.1.2/24 dev <your-nic>
sudo ip link set <your-nic> up
```

The connection is a direct cable between the host and the MR18—no switch or router.
