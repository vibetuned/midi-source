#!/usr/bin/env python3
"""Capture pad hits as raw samples from every analog input, and measure them.

  tools/hits.py                       capture 1 hit
  tools/hits.py --count 12 --timeout 180 --label sweep

For each hit (console x: 1.5 ms before the first threshold crossing to 2.5 ms
after, every sample of every input), it prints per input:
  up/down  how far the signal rose above / fell below its resting level (a
           biased pad measures both; a plain one reads 0 below rest)
  rise     when the signal last crossed 25% of its own peak on the way up to
           it, in microseconds after the earliest input (corrected for the ADC
           taking the inputs in turn, 2 us apart); "-" when the input only
           heard a little (often it swung below rest, which reads as 0)
They are what locating a hit works from: the piezo nearest the hit hears it
first and loudest.

Each hit's samples are saved as build/hits/<time>-<label>-<n>.csv. Close
tools/scope.py first: only one program can read the console.
"""

import argparse
import os
import select
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from console import find_port, open_port  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "build" / "hits"


def read_capture(fd, deadline):
    """Lines from 'X pad ...' to 'X end'; None on timeout or failure."""
    buf, lines, started = b"", [], False
    while time.monotonic() < deadline:
        if not select.select([fd], [], [], 0.05)[0]:
            continue
        buf += os.read(fd, 65536)
        *done, buf = buf.split(b"\n")
        for raw in done:
            line = raw.decode(errors="replace").strip()
            if line.startswith("X failed"):
                print("  " + line)
                return None
            if line.startswith("X pad"):
                started = True
            if started:
                lines.append(line)
                if line == "X end":
                    return lines
    return None


def rise(col, peak_i, level):
    """Fractional index where `col` last crossed `level` on its way up to the peak."""
    k = peak_i
    while k > 0 and col[k - 1] >= level:
        k -= 1
    if k == 0 or col[k] == col[k - 1]:
        return float(k)
    return k - 1 + (level - col[k - 1]) / (col[k] - col[k - 1])


def analyse(lines):
    head = dict(zip(lines[0].split()[1::2], lines[0].split()[2::2]))
    words = lines[1].split()
    k = words.index("slots")
    inputs, slots = [int(w) for w in words[2:k]], [int(w) for w in words[k + 1:]]
    rate, slot_ns, before = int(head["rate"]), int(head["slot_ns"]), int(head["before"])
    rows = [[int(v) for v in l.split()] for l in lines[2:-1]]
    columns = [[r[1 + i] for r in rows] for i in range(len(inputs))]
    quiet = max(8, before // 4)  # the start of the window, before anything moves
    result = []
    for adc, slot, col in zip(inputs, slots, columns):
        base = statistics.median(col[:quiet])
        noise = max(abs(v - base) for v in col[:quiet])
        peak_i = max(range(len(col)), key=col.__getitem__)
        amp = col[peak_i] - base
        dip = base - min(col)  # the swing below rest: measurable on a biased pad, clipped at 0 otherwise
        t = None
        if amp >= max(3 * noise, 60):
            t = rise(col, peak_i, base + 0.25 * amp) * 1e6 / rate + slot * slot_ns / 1000
        result.append({"input": f"GP{26 + adc}", "peak": col[peak_i], "amp": amp, "dip": dip, "rest": base,
                       "rise": t})
    return head, inputs, rows, result


def show(n, head, result):
    times = [r["rise"] for r in result if r["rise"] is not None]
    t0 = min(times) if times else 0
    loudest = max(result, key=lambda r: r["amp"])
    print(f"hit {n}: loudest {loudest['input']}; pad {head['pad']} (GP{26 + int(head['adc'])}) crossed its threshold first")
    for r in result:
        rel = "     -" if r["rise"] is None else f"{r['rise'] - t0:6.0f}"
        print(f"  {r['input']}  rest {r['rest']:5.0f}  up +{r['amp']:5.0f} ({r['amp'] / loudest['amp']:4.0%})"
              f"  down -{r['dip']:4.0f}  rise {rel} us")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", type=int, default=1, help="hits to capture (default 1)")
    ap.add_argument("--timeout", type=float, default=60, help="seconds to wait in all (default 60)")
    ap.add_argument("--label", default="hit", help="name for the saved files, e.g. where you hit")
    ap.add_argument("--port")
    args = ap.parse_args()

    fd = open_port(args.port or find_port())
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    deadline = time.monotonic() + args.timeout
    got = 0
    while got < args.count and time.monotonic() < deadline:
        os.write(fd, b"x")
        lines = read_capture(fd, deadline)
        if not lines:
            continue
        got += 1
        head, inputs, rows, result = analyse(lines)
        show(got, head, result)
        path = OUT / f"{stamp}-{args.label}-{got}.csv"
        with open(path, "w") as f:
            f.write(f"# {lines[0]}\n# {lines[1]}\n")
            f.write("sample," + ",".join(f"GP{26 + a}" for a in inputs) + "\n")
            for r in rows:
                f.write(",".join(map(str, r)) + "\n")
        time.sleep(0.3)  # let the pad ring down before arming again
    print(f"[{got} of {args.count} hits captured; samples in {OUT}]")
    os.close(fd)


if __name__ == "__main__":
    main()
