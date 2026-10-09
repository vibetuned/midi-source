"""Locating strikes on a pad read through several piezos: the pad's geometry,
the calibration spots and the model. Shared by tools/calibrate.py (in the
terminal) and the scope's Pad tab (interactive).

A strike's levels are each piezo's signed level: how far its signal rose above
rest, or fell below it if that swing was larger (on a biased pad the board
bending away from a piezo reads below rest). Divided by their length they make
the strike's signature: the same wherever it lands, however hard. A board bends
like a plate, not like ripples from a point (a piezo 7 cm away reads up or down
depending on direction), so instead of a formula of distance the model is
learned from the calibration spots: a linear map from signature to place,

    (x, y) = [signature, 1] @ W           (W is 4 x 2, least squares, lightly ridged)

small enough for the firmware (src/pad_calibration.h).

The geometry is the Evans pad's: two piezos 7 cm apart near the top, the third
10 cm from each, below them. Which pin is which piezo comes from the first
three spots, each right above one piezo: that piezo reads the highest level.

The same piezos may be read by two ADCs at once, the Pico's (pins GP26-GP28)
and an MCP3008 (CH0-CH7): each ADC's pins are matched to the piezos on their
own, and a map is made from each, and from both, out of the same strikes.
"""

import json
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "build" / "calibration"

TOP, SIDE = 7.0, 10.0  # cm: between the top piezos, and from each to the bottom one
H = math.sqrt(SIDE ** 2 - (TOP / 2) ** 2)
PIEZOS = {"top-left": (-TOP / 2, 0.0), "top-right": (TOP / 2, 0.0), "bottom": (0.0, -H)}
SPOTS = [
    ("top-left", "directly above the top-left piezo", PIEZOS["top-left"]),
    ("top-right", "directly above the top-right piezo", PIEZOS["top-right"]),
    ("bottom", "directly above the bottom piezo", PIEZOS["bottom"]),
    ("top-mid", "midway between the two top piezos", (0.0, 0.0)),
    ("left-mid", "midway along the left side, top-left to bottom", (-TOP / 4, -H / 2)),
    ("right-mid", "midway along the right side, top-right to bottom", (TOP / 4, -H / 2)),
    ("centre", "the centre of the triangle (a third of the way down, centred)", (0.0, -H / 3)),
]
CORNERS = 3            # the first spots, each right above the piezo of the same name
ORDER = list(PIEZOS)   # signature order: top-left, top-right, bottom
RIDGE = 0.1
ADCS = {"GP": "Pico ADC", "CH": "MCP3008"}  # pin prefix -> the ADC that reads it
SILENT = 40            # a level no strike on a wired piezo stays under (12-bit counts)

# A strike, everywhere below, is {pin: signed level}, e.g. {"GP26": 812, "GP27": -150, "GP28": 301};
# spots_hits is [(spot, [strike, ...]), ...] with spot an entry of SPOTS. A map
# reads `pins`, a list of them in signature order.


def adc_of(pin):
    return pin[:2]


def firmware_input(pin):
    """The pin as src/layout.c names the analog input: "GP27" -> "1", "CH2" -> "MCP3008_CH(2)"."""
    return str(int(pin[2:]) - 26) if adc_of(pin) == "GP" else f"MCP3008_CH({int(pin[2:])})"


def signature(levels):
    """Signed levels -> unit-length vector, or None if silent."""
    v = np.array(levels, dtype=float)
    n = np.linalg.norm(v)
    return v / n if n > 0 else None


def samples(spots_hits, pins):
    """(signature rows, positions, spot index) for every strike."""
    xs, ys, idx = [], [], []
    for i, ((_, _, pos), strikes) in enumerate(spots_hits):
        for strike in strikes:
            sig = signature([strike[pin] for pin in pins])
            if sig is not None:
                xs.append(sig)
                ys.append(pos)
                idx.append(i)
    return np.array(xs), np.array(ys, dtype=float), np.array(idx)


def fit(xs, ys):
    a = np.hstack([xs, np.ones((len(xs), 1))])
    return np.linalg.solve(a.T @ a + RIDGE * np.eye(a.shape[1]), a.T @ ys)  # 4 x 2


def locate(w, sig):
    return tuple(float(v) for v in np.append(sig, 1.0) @ w)


def loudest_pin(strikes):
    """The pin most often reading the highest level over these strikes."""
    votes = {}
    for strike in strikes:
        pin = max(strike, key=strike.get)
        votes[pin] = votes.get(pin, 0) + 1
    return max(votes, key=votes.get)


def assign_pins(spots_hits):
    """({pin: piezo}, problems) from the corner spots, each ADC's pins on their
    own. An ADC whose corners don't each have their own loudest pin is left out
    (with a problem saying so), as is one that hears nothing (not connected)."""
    mapping, problems = {}, []
    pins = sorted({pin for _, strikes in spots_hits for strike in strikes for pin in strike})
    for adc, name in ADCS.items():
        mine = [pin for pin in pins if adc_of(pin) == adc]
        corners = [[{pin: s[pin] for pin in mine} for s in strikes] for _, strikes in spots_hits[:CORNERS]]
        if not mine or len(corners) < CORNERS:
            continue
        if max(abs(v) for strikes in corners for s in strikes for v in s.values()) < SILENT:
            problems.append(f"{name}: no signal on {', '.join(mine)} (not connected?)")
            continue
        found = {loudest_pin(strikes): spot[0] for (spot, _), strikes in zip(spots_hits, corners)}
        if len(found) != CORNERS:
            problems.append(f"{name}: the corners didn't each have their own loudest pin ({found})")
            continue
        mapping.update(found)
    return mapping, problems


def detect_mapping(spots_hits):
    """{pin: piezo} from the corner spots; ValueError if no ADC's pins could be matched."""
    mapping, problems = assign_pins(spots_hits)
    if not mapping:
        raise ValueError("; ".join(problems) or "no strikes at the corner spots")
    return mapping


def maps(mapping):
    """The maps worth making: [(label, pins)] for each ADC whose pins cover
    the piezos, then (if there are two) for both together."""
    found = []
    for adc, name in ADCS.items():
        pins = sorted((pin for pin in mapping if adc_of(pin) == adc), key=lambda pin: ORDER.index(mapping[pin]))
        if [mapping[pin] for pin in pins] == ORDER:
            found.append((f"{name} ({', '.join(pins)})", pins))
    if len(found) > 1:
        found.append(("both", [pin for _, pins in found for pin in pins]))
    return found


def summary(errors):
    errors = sorted(errors)
    return {"median": errors[len(errors) // 2], "worst": errors[-1]}


def evaluate(spots_hits, pins):
    """How well strikes are located by a map reading `pins`. Each spot's
    strikes are located by a map fitted without them ("located", "between":
    what to expect away from the spots), and by the map from all of them
    ("fitted", "at")."""
    xs, ys, idx = samples(spots_hits, pins)
    full = fit(xs, ys)
    spots, between, at = [], [], []
    for i, ((name, _, pos), _) in enumerate(spots_hits):
        w = fit(xs[idx != i], ys[idx != i])
        found = [locate(w, s) for s in xs[idx == i]]
        fitted = [locate(full, s) for s in xs[idx == i]]
        errs = [math.dist(f, pos) for f in found]
        fitted_errs = [math.dist(f, pos) for f in fitted]
        between += errs
        at += fitted_errs
        spots.append({"name": name, "pos": pos, "located": found, "error": sum(errs) / len(errs),
                      "fitted": fitted, "fitted_error": sum(fitted_errs) / len(fitted_errs)})
    return {"spots": spots, "between": summary(between), "at": summary(at), "map": full}


def calibration(mapping, pins, ev, source):
    """What calibration.json holds, for the map reading `pins`."""
    return {"pins": mapping, "piezos_cm": PIEZOS, "spots_cm": {name: pos for name, _, pos in SPOTS},
            "signature_pins": pins, "signature_order": [mapping[pin] for pin in pins],
            "signature_map": ev["map"].tolist(), "error_cm": ev["between"], "error_at_spots_cm": ev["at"],
            "source": source, "model": "(x, y) = [signed levels / their length, 1] @ signature_map"}


def calibrate_all(spots_hits, source):
    """(problems, [(label, calibration, evaluation)]) for every map worth making."""
    mapping, problems = assign_pins(spots_hits)
    if not mapping:
        raise ValueError("; ".join(problems) or "no strikes at the corner spots")
    found = []
    for label, pins in maps(mapping):
        ev = evaluate(spots_hits, pins)
        found.append((label, calibration(mapping, pins, ev, source), ev))
    return problems, found


def calibrate(spots_hits, source):
    """The first map's calibration (the Pico's ADC if it was matched) and its evaluation."""
    _, found = calibrate_all(spots_hits, source)
    _, cal, ev = found[0]
    return cal, ev


def signature_pins(cal):
    """The pins a calibration's map reads, in order (older files list piezos only)."""
    if "signature_pins" in cal:
        return cal["signature_pins"]
    pin_of = {piezo: pin for pin, piezo in cal["pins"].items()}
    return [pin_of[piezo] for piezo in cal["signature_order"]]


def save(out, cal):
    """calibration.json in the run's directory, and src/pad_calibration.h; returns the header's path."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "calibration.json").write_text(json.dumps(cal, indent=2))
    return write_header(cal, out.name)


def newest():
    """(path, calibration) of the newest build/calibration/*/calibration.json, or (None, None)."""
    for path in sorted(RUNS.glob("*/calibration.json"), reverse=True):
        try:
            cal = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if "signature_map" in cal:
            return path, cal
    return None, None


def write_header(cal, run):
    """src/pad_calibration.h: what the firmware's zone pad needs."""
    pins = signature_pins(cal)
    rows = ", ".join("{%.5ff, %.5ff}" % tuple(r) for r in cal["signature_map"])
    centre = [sum(c[i] for c in cal["piezos_cm"].values()) / 3 for i in (0, 1)]
    err = cal["error_cm"]
    path = ROOT / "src" / "pad_calibration.h"
    path.write_text(f"""// Generated from build/calibration/{run} (tools/scope.py's Pad tab, or
// tools/calibrate.py). Don't edit it: calibrate again, then build and flash.
#ifndef PAD_CALIBRATION_H
#define PAD_CALIBRATION_H

// The zone pad's piezos in the calibration's order: {", ".join(f"{cal['pins'][pin]} {pin}" for pin in pins)}.
#define PAD_CALIBRATION_PIEZOS {len(pins)}
#define PAD_CALIBRATION_ADC {{{", ".join(firmware_input(pin) for pin in pins)}}}

// Signature (each piezo's signed level over their length, in the order above)
// and 1, to (x, y) in cm. Between calibration spots hits land within about
// {err["median"]:.1f} cm, at worst {err["worst"]:.1f} cm.
#define PAD_CALIBRATION_MAP {{{rows}}}

// The centre of the piezos' triangle, cm: the default split between zones.
#define PAD_CENTRE_X {centre[0]:.2f}f
#define PAD_CENTRE_Y {centre[1]:.2f}f

#define PAD_CALIBRATION_RUN "{run}"

#endif
""")
    return path
