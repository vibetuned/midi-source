#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy>=1.26"]
# ///
"""Calibrate hit location on a pad with three piezos under it, in the terminal.

    uv run tools/calibrate.py            (close tools/scope.py first)
    uv run tools/calibrate.py --refit build/calibration/<run>   (re-analyse saved hits)

The scope's Pad tab does the same interactively, from the board's own
measurement of each strike; this one works from raw captures (console x), so
it also calibrates a board without a zone pad.

It asks you to hit seven known spots, four medium hits each, and captures every
hit's raw samples. Each hit's signature is its three signed levels divided by
their length; the calibration learns a linear map from signature to place
(tools/padmap.py). Every calibration hit is then located by a map made without
its spot, so the reported errors are what to expect between spots. Everything
is saved in build/calibration/<time>/: the hits' samples and calibration.json,
which tools/scope.py's Pad tab reads, and src/pad_calibration.h for the firmware.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from console import find_port, open_port  # noqa: E402
from hits import analyse, read_capture  # noqa: E402
import padmap  # noqa: E402
from padmap import ROOT, SPOTS  # noqa: E402


def signed(r):
    """A captured input's signed level: its rise above rest, or its dip below if larger."""
    return r["amp"] if r["amp"] >= r.get("dip", 0) else -r["dip"]


def strike(result):
    """hits.analyse's result -> {pin: signed level}."""
    return {r["input"]: signed(r) for r in result}


def capture(fd, count, timeout):
    got = []
    deadline = time.monotonic() + timeout
    while len(got) < count and time.monotonic() < deadline:
        os.write(fd, b"x")
        lines = read_capture(fd, deadline)
        if not lines:
            continue
        head, inputs, rows, result = analyse(lines)
        got.append((lines, result))
        levels = strike(result)
        print(f"    hit {len(got)}: " + "  ".join(f"{pin} {v:+5.0f}" for pin, v in levels.items())
              + f"   (loudest {max(levels, key=levels.get)})")
        time.sleep(0.3)
    return got


def report(spots_hits, out, source):
    try:
        cal, ev = padmap.calibrate(spots_hits, source)
    except ValueError as e:
        sys.exit(f"{e}; redo the first spots")
    print("\nWhich pin is which piezo: " + ", ".join(f"{pin} = {p}" for pin, p in sorted(cal["pins"].items())))
    print("\nHow well hits are located (each spot located by a map fitted without it):")
    print("  spot         located at (cm)                         error (cm)")
    for spot in ev["spots"]:
        print(f"  {spot['name']:11}  {' '.join(f'({x:4.1f},{y:5.1f})' for x, y in spot['located']):48}  "
              f"{spot['error']:4.1f}")
    between, at = ev["between"], ev["at"]
    print(f"  all hits: median error {between['median']:.1f} cm, worst {between['worst']:.1f} cm"
          f"  (with the map from every spot: {at['median']:.1f} cm, worst {at['worst']:.1f} cm)")
    header = padmap.save(out, cal)
    print(f"\nSaved {out / 'calibration.json'} (tools/scope.py's Pad tab uses the newest one)")
    print(f"Wrote {header.relative_to(ROOT)}: Build & Flash to put it in the firmware")


# --- the session -----------------------------------------------------------------------

def save(out, n, name, lines):
    path = out / f"{n:02d}-{name}.txt"
    path.write_text("\n".join(lines) + "\n")


def load(out):
    """A saved run's spots and strikes: the scope's hits.json, or this tool's captures."""
    if (out / "hits.json").exists():
        saved = {s["name"]: s["hits"] for s in json.loads((out / "hits.json").read_text())["spots"]}
        return [(spot, [h["levels"] for h in saved[spot[0]]]) for spot in SPOTS if saved.get(spot[0])]
    spots_hits = []
    for spot in SPOTS:
        hits = [strike(analyse(p.read_text().splitlines())[3]) for p in sorted(out.glob(f"*-{spot[0]}.txt"))]
        if hits:
            spots_hits.append((spot, hits))
    return spots_hits


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hits", type=int, default=4, help="hits per spot (default 4)")
    ap.add_argument("--refit", metavar="DIR", help="re-analyse a saved run instead of capturing")
    ap.add_argument("--port")
    args = ap.parse_args()

    if args.refit:
        out = Path(args.refit)
        source = json.loads((out / "calibration.json").read_text()).get("source", "console captures") \
            if (out / "calibration.json").exists() else "console captures"
        report(load(out), out, source)
        return

    out = padmap.RUNS / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    fd = open_port(args.port or find_port())
    print(__doc__.split("\n\n")[0])
    print(f"Seven spots, {args.hits} medium hits each, about a second apart. Saving to {out}\n")
    spots_hits, n = [], 0
    for i, (name, text, pos) in enumerate(SPOTS, 1):
        input(f"Spot {i}/{len(SPOTS)}: {text}.  Press Enter, then hit {args.hits} times… ")
        while True:
            got = capture(fd, args.hits, timeout=60)
            if len(got) == args.hits:
                break
            print(f"    only {len(got)} hits in 60 s; let's do this spot again")
        for lines, _ in got:
            n += 1
            save(out, n, name, lines)
        spots_hits.append(((name, text, pos), [strike(result) for _, result in got]))
    os.close(fd)
    report(spots_hits, out, "console captures")


if __name__ == "__main__":
    main()
