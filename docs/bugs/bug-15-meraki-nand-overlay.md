# Bug 15: Meraki NAND Overlay

**Category**: Failsafe
**Related docs**: [script-reference](../reference/script-reference.md)

## Symptom

The device sent Meraki management frames (ethertype `0x0642`) instead of responding as an OpenWrt device. All TCP ports appeared filtered. No telnet (port 23), no HTTP (port 80), no SSH (port 22).

## Root Cause

OpenWrt's preinit process includes a step that searches for and mounts an overlay filesystem. On the MR18, the NAND flash contains the Meraki/Cisco filesystem. OpenWrt's preinit found this partition and mounted it as an overlay over the initramfs root.

The mount sequence:

1. OpenWrt kernel boots from initramfs in RAM
2. Preinit runs `/lib/preinit/` scripts in order
3. One of the preinit scripts probes NAND for a writable overlay partition
4. The Meraki filesystem on NAND is found and mounted as overlayfs
5. The Meraki `/etc/init.d/`, `/usr/bin/`, and service configurations shadow the initramfs versions
6. OpenWrt's init proceeds, but it is now running Meraki daemons from the overlay

The Meraki userspace starts its cloud management daemon, which generates the `0x0642` ethertype frames. The Meraki daemon also configures the network interface with Meraki's settings (DHCP client for cloud connectivity), not OpenWrt's static `192.168.1.1`.

## Fix

Trigger failsafe mode during the preinit window. When failsafe is activated, OpenWrt's preinit explicitly skips the overlay mount step and brings up a minimal environment:

- Network: static `192.168.1.1/24` on `eth0`
- Services: `telnetd` on port 23 (no authentication)
- Filesystem: initramfs only (no overlay)
- Init: minimal shell environment, no full service startup

From failsafe, run `sysupgrade -n` with the OpenWrt sysupgrade image to replace the Meraki NAND contents with OpenWrt's filesystem.

## Lesson

NAND overlays can completely override initramfs behavior. An initramfs kernel only guarantees that the kernel is yours—the userspace may be replaced by overlay mounts from the existing flash contents. On devices with factory firmware, always plan for failsafe mode to bypass the overlay.
