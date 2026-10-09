#!/usr/bin/env python3
"""Talk to a running midi-source's console without a terminal program (macOS).

  tools/console.py s p              send console commands, print the replies
  tools/console.py --record 3       record the next 3 pad hits (console w)
  tools/console.py --port /dev/cu.usbmodem21103 s

The port is found automatically when one midi-source board is connected.
Output is printed as it arrives, and the tool exits on its own: after the
replies go quiet, or after the recordings, or at --timeout.
"""

import argparse
import os
import select
import sys
import termios
import time
import tty

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from boards import MIDI_SOURCE, usb_devices  # noqa: E402

RECORDING_END = "above the threshold"  # last line of a pad recording


def find_port():
    ports = [d["ports"][0] for d in usb_devices() if (d["vid"], d["pid"]) == MIDI_SOURCE and d["ports"]]
    if len(ports) != 1:
        sys.exit(f"{len(ports)} midi-source boards connected; pass --port (see tools/boards.py)")
    return ports[0]


def open_port(path):
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    tty.setraw(fd)
    attrs = termios.tcgetattr(fd)
    attrs[4] = attrs[5] = termios.B115200  # never 1200: that reboots the board into BOOTSEL
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    return fd


def read_until(fd, deadline, quiet=None, stop=None, seen=""):
    """Print incoming text until the deadline, `quiet` seconds without data,
    or until `stop(text so far)` is true. Returns everything read."""
    text, last = seen, time.monotonic()
    while time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.05)
        if ready:
            chunk = os.read(fd, 4096).decode(errors="replace").replace("\r\n", "\n")
            if chunk:
                sys.stdout.write(chunk)
                sys.stdout.flush()
                text += chunk
                last = time.monotonic()
                if stop and stop(text):
                    break
        elif quiet and time.monotonic() - last > quiet:
            break
    return text


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("commands", nargs="*", help="console commands, e.g. s p (h lists them)")
    ap.add_argument("--port")
    ap.add_argument("--record", type=int, metavar="N", help="record the next N pad hits")
    ap.add_argument("--timeout", type=float, default=10, help="seconds before giving up (default 10)")
    args = ap.parse_args()

    fd = open_port(args.port or find_port())
    deadline = time.monotonic() + args.timeout
    text = read_until(fd, min(deadline, time.monotonic() + 0.5), quiet=0.3)  # the banner

    for c in args.commands:
        os.write(fd, c.encode())
        text = read_until(fd, deadline, quiet=0.3, seen=text)

    if args.record:
        done = text.count(RECORDING_END)
        target = done + args.record
        while done < target and time.monotonic() < deadline:
            os.write(fd, b"w")
            text = read_until(fd, deadline, stop=lambda t: t.count(RECORDING_END) > done, seen=text)
            done = text.count(RECORDING_END)
        print(f"[{done - (target - args.record)} of {args.record} hits recorded]")
    os.close(fd)


if __name__ == "__main__":
    main()
