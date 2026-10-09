#!/usr/bin/env python3
"""List the Pico boards on USB (macOS).

  tools/boards.py            table of every Pico: running midi-source, in
                             BOOTSEL, or running other firmware
  tools/boards.py --running  one "serial console-port" line per running
                             midi-source board (used by flash.sh)
"""

import plistlib
import subprocess
import sys

MIDI_SOURCE = (0xCAFE, 0x4009)  # src/usb_descriptors.c
RASPBERRY_PI = 0x2E8A
BOOTSEL = {0x0003: "RP2040", 0x000F: "RP2350"}


def usb_devices():
    """Every USB device with the serial ports beneath it, hubs included."""
    out = subprocess.run(["ioreg", "-a", "-r", "-c", "IOUSBHostDevice", "-l"],
                         capture_output=True, check=True).stdout
    devices = []

    def walk(node):
        if node.get("IOObjectClass") == "IOUSBHostDevice":
            device = {
                "vid": node.get("idVendor", 0),
                "pid": node.get("idProduct", 0),
                "product": node.get("kUSBProductString") or node.get("USB Product Name") or "?",
                "serial": node.get("USB Serial Number") or "",
                "ports": [],
            }
            devices.append(device)
            collect(node, device)
        else:
            for child in node.get("IORegistryEntryChildren", []):
                walk(child)

    def collect(node, device):
        for child in node.get("IORegistryEntryChildren", []):
            if child.get("IOObjectClass") == "IOUSBHostDevice":
                walk(child)  # a device behind a hub owns its own ports
                continue
            if "IOCalloutDevice" in child:
                device["ports"].append(child["IOCalloutDevice"])
            collect(child, device)

    for root in plistlib.loads(out) if out.strip() else []:
        walk(root)
    return devices


def main():
    devices = usb_devices()
    running = [d for d in devices if (d["vid"], d["pid"]) == MIDI_SOURCE]

    if "--running" in sys.argv[1:]:
        for d in running:
            print(d["serial"], d["ports"][0] if d["ports"] else "-")
        return

    rows = []
    for d in running:
        rows.append(("midi-source", d["serial"], ", ".join(d["ports"]) or "-"))
    for d in devices:
        if d["vid"] == RASPBERRY_PI and d["pid"] in BOOTSEL:
            rows.append(("BOOTSEL " + BOOTSEL[d["pid"]], d["serial"], "-"))
        elif d["vid"] == RASPBERRY_PI:
            rows.append(("other: " + d["product"], d["serial"], ", ".join(d["ports"]) or "-"))

    if not rows:
        print("No Pico boards on USB.")
        return
    print(f"{'STATE':<24} {'SERIAL':<18} CONSOLE")
    for state, serial, ports in rows:
        print(f"{state:<24} {serial:<18} {ports}")


if __name__ == "__main__":
    main()
