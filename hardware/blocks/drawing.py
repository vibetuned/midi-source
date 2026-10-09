"""Draws a SKiDL circuit as one readable KiCad sheet.

SKiDL decides what connects to what; this module only decides where things
go. Each block draws itself into a frame (its `draw(sheet, x, y)`), placing
its parts by hand and wiring their pins; the Pico is drawn on the left with a
net label, power symbol or no-connect flag on every pin. Blocks meet the Pico
through net labels (GP15, GP27...) and power symbols (GND, GNDA, +3V3).

Coordinates are millimetres, y pointing down, on KiCad's 1.27 mm grid.
"""

import math
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Callable

from .sexp import Str, child, children, dumps, parse

LIBRARIES = "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols"
POWER_NETS = {"GND", "GNDA", "+3V3"}  # drawn as KiCad power symbols
# Nets that only a module's power input pin sits on (the module makes them
# itself): a PWR_FLAG tells KiCad's ERC they are powered.
FLAGGED_NETS = {"ADC_VREF"}
FONT = ["effects", ["font", ["size", "1.27", "1.27"]]]
PAPERS = [("A4", 297, 210), ("A3", 420, 297), ("A2", 594, 420)]


@dataclass
class Block:
    """What a block function returns: its frame size and how to draw it."""
    title: str
    width: float
    height: float
    draw: Callable  # draw(sheet, x, y): (x, y) is the frame's top-left corner


_libs = {}


def lib_symbol(lib, name):
    """The symbol definition from KiCad's library, renamed lib:name. A symbol
    that extends another (the MCP3008 extends the MCP3208) gets its parent's
    body and pins under its own properties: a schematic embeds it whole."""
    if lib not in _libs:
        tree = parse(open(f"{LIBRARIES}/{lib}.kicad_sym").read())
        _libs[lib] = {s[1]: s for s in children(tree, "symbol")}
    sym = _libs[lib][name]
    extends = child(sym, "extends")
    if extends:
        parent = lib_symbol(lib, extends[1])
        props, body = children(sym, "property"), []
        for item in parent[2:]:
            if isinstance(item, list) and item[0] == "property":
                if props:  # the derived symbol's properties, where the parent's were
                    body += props
                    props = []
            elif isinstance(item, list) and item[0] == "symbol":  # units: <name>_<unit>_<style>
                body.append([item[0], Str(name + item[1][len(extends[1]):])] + item[2:])
            else:
                body.append(item)
        sym = [sym[0], name] + body
    return [sym[0], Str(f"{lib}:{name}")] + sym[2:]


def symbol_pins(lib, name):
    """Pin number -> (x, y, angle) in library coordinates (y up)."""
    pins = {}
    for unit in children(lib_symbol(lib, name), "symbol"):
        for p in children(unit, "pin"):
            at = child(p, "at")
            pins[str(child(p, "number")[1])] = (float(at[1]), float(at[2]), int(float(at[3])))
    return pins


def transform(lx, ly, rot):
    """Library point to sheet offset for a symbol rotated `rot` degrees
    counter-clockwise (the library's y axis points up, the sheet's down)."""
    x, y = lx, -ly
    a = math.radians(rot)
    return (round(x * math.cos(a) + y * math.sin(a), 4), round(-x * math.sin(a) + y * math.cos(a), 4))


def fmt(v):
    return f"{v:.4f}".rstrip("0").rstrip(".")


class Sheet:
    def __init__(self, name, title):
        self.name, self.title = name, title
        self.uuid = self._uuid("sheet")
        self.items = []
        self.lib_ids = {}
        self.points = Counter()  # connection points: wire ends and pins
        self.power_count = 0
        self.extent = (0.0, 0.0)

    def _uuid(self, *key):
        # Stable across runs, so regenerating doesn't churn the file.
        return str(uuid.uuid5(uuid.NAMESPACE_URL, "midi-source/" + "/".join(map(str, (self.name,) + key))))

    def _grow(self, x, y):
        self.extent = (max(self.extent[0], x), max(self.extent[1], y))

    def _symbol(self, lib, name, ref, value, footprint, x, y, rot, text_at, hide_ref=False,
                value_at=None, justify="left"):
        lib_id = f"{lib}:{name}"
        self.lib_ids[lib_id] = (lib, name)
        pins = symbol_pins(lib, name)
        tx, ty = text_at
        vx, vy = value_at if value_at else (tx, ty + 2.54)
        # KiCad turns a field with its symbol, then keeps the text readable by
        # flipping its justification instead of drawing it upside down. Seen in
        # KiCad 10: at 90 and 270 a field shows level when its angle matches the
        # symbol's; at 180 and 270 left and right justification come out swapped.
        angle = str(rot) if rot in (90, 270) else "0"
        if rot in (180, 270) and justify in ("left", "right"):
            justify = "right" if justify == "left" else "left"
        just = [] if justify == "center" else [["justify", justify]]
        props = [
            ["property", Str("Reference"), Str(ref), ["at", fmt(x + tx), fmt(y + ty), angle],
             FONT + ([["hide", "yes"]] if hide_ref else []) + just],
            ["property", Str("Value"), Str(value), ["at", fmt(x + vx), fmt(y + vy), angle], FONT + just],
            ["property", Str("Footprint"), Str(footprint or ""), ["at", fmt(x), fmt(y), "0"], FONT + [["hide", "yes"]]],
            ["property", Str("Datasheet"), Str(""), ["at", fmt(x), fmt(y), "0"], FONT + [["hide", "yes"]]],
        ]
        self.items.append(
            ["symbol", ["lib_id", Str(lib_id)], ["at", fmt(x), fmt(y), str(rot)], ["unit", "1"],
             ["exclude_from_sim", "no"], ["in_bom", "no" if hide_ref else "yes"],
             ["on_board", "no" if hide_ref else "yes"], ["dnp", "no"], ["uuid", self._uuid(ref)]]
            + props
            + [["pin", Str(n), ["uuid", self._uuid(ref, "pin", n)]] for n in pins]
            + [["instances", ["project", Str(self.name),
                              ["path", Str("/" + self.uuid), ["reference", Str(ref)], ["unit", "1"]]]]])
        self._grow(x + 15, y + 15)
        return {n: (round(x + transform(px, py, rot)[0], 4), round(y + transform(px, py, rot)[1], 4))
                for n, (px, py, _) in pins.items()}

    # --- what blocks use -------------------------------------------------------

    def place(self, part, x, y, rot=0, text_at=(2.54, -1.27)):
        """Place a SKiDL part; returns pin number -> sheet point. `text_at` is
        where the reference goes, relative to the symbol; the value goes below."""
        lib = part.lib.filename
        pins = self._symbol(lib, part.name, part.ref, str(part.value), part.footprint, x, y, rot, text_at)
        for pin in part.pins:
            if pin.is_connected():
                self.points[pins[str(pin.num)]] += 1
        return pins

    def wire(self, *points):
        """A polyline through `points`; joints are wire ends, so they connect."""
        for a, b in zip(points, points[1:]):
            self.items.append(["wire", ["pts", ["xy", fmt(a[0]), fmt(a[1])], ["xy", fmt(b[0]), fmt(b[1])]],
                               ["stroke", ["width", "0"], ["type", "default"]],
                               ["uuid", self._uuid("wire", a, b)]])
            self.points[a] += 1
            self.points[b] += 1

    def label(self, net, at, side="left"):
        """A net label whose text runs to the `side` of its connection point."""
        self.items.append(["label", Str(net), ["at", fmt(at[0]), fmt(at[1]), "180" if side == "left" else "0"],
                           FONT + [["justify", "right" if side == "left" else "left"]],
                           ["uuid", self._uuid("label", net, at)]])
        self.points[at] += 1

    def power(self, net, at, rot=0):
        """A power symbol (GND, GNDA, +3V3) whose pin is at `at`. Ground
        symbols hang down at rotation 0, +3V3 points up."""
        self.power_count += 1
        body = (0, 1) if net.startswith("GND") else (0, -1)  # where the symbol sits, at rotation 0
        dx, dy = transform(body[0], -body[1], rot)
        # The name goes just beyond the symbol, on the side it points to.
        justify = "center" if abs(dy) > 0.5 else ("left" if dx > 0 else "right")
        value_at = (round(dx * 4.445, 4), round(dy * 4.445, 4))
        self._symbol("power", net, f"#PWR{self.power_count:02d}", net, "", at[0], at[1], rot,
                     value_at, hide_ref=True, value_at=value_at, justify=justify)
        self.points[at] += 1

    def flag(self, at):
        """KiCad's PWR_FLAG: the net at `at` is powered (by something off the sheet)."""
        self.power_count += 1
        self._symbol("power", "PWR_FLAG", f"#FLG{self.power_count:02d}", "PWR_FLAG", "", at[0], at[1], 0,
                     (0, -5.08), hide_ref=True, value_at=(0, -4.445), justify="center")
        self.points[at] += 1

    def no_connect(self, at):
        self.items.append(["no_connect", ["at", fmt(at[0]), fmt(at[1])], ["uuid", self._uuid("nc", at)]])

    def frame(self, x, y, w, h, title):
        self.items.append(["rectangle", ["start", fmt(x), fmt(y)], ["end", fmt(x + w), fmt(y + h)],
                           ["stroke", ["width", "0"], ["type", "dash"]], ["fill", ["type", "none"]],
                           ["uuid", self._uuid("frame", title)]])
        self.items.append(["text", Str(title), ["exclude_from_sim", "no"], ["at", fmt(x + 1.27), fmt(y + 2.54), "0"],
                           ["effects", ["font", ["size", "1.524", "1.524"], ["bold", "yes"]], ["justify", "left"]],
                           ["uuid", self._uuid("title", title)]])
        self._grow(x + w, y + h)

    # --- the Pico ----------------------------------------------------------------

    def draw_pico(self, pico, x, y):
        """The module with every pin resolved: label, power symbol or no-connect."""
        part = pico.part
        pins = self._symbol(part.lib.filename, part.name, part.ref, str(part.value), part.footprint,
                            x, y, 0, (-22.86, -43.18))
        geometry = symbol_pins(part.lib.filename, part.name)
        done = set()
        for pin in part.pins:
            at = pins[str(pin.num)]
            if at in done:  # the GND pins share one point
                continue
            done.add(at)
            if not pin.is_connected():
                self.no_connect(at)
                continue
            # Outward from the body: the pin's angle points into it.
            out = {0: (-1, 0), 180: (1, 0), 90: (0, 1), 270: (0, -1)}[geometry[str(pin.num)][2]]
            net = pin.net.name
            self.points[at] += 1
            if net in POWER_NETS:
                # Power symbols stand upright: grounds hang below, +3V3 above.
                # From a side pin the stub turns down (or up) to reach it.
                up = -1 if net.startswith("+") else 1
                end = (round(at[0] + 2.54 * out[0], 4), round(at[1] + 2.54 * out[1], 4))
                if out[0]:
                    turn = (end[0], round(end[1] + 2.54 * up, 4))
                    self.wire(at, end, turn)
                    end = turn
                else:
                    self.wire(at, end)
                self.power(net, end, 0 if out[1] in (0, up) else 180)
            else:
                end = (round(at[0] + 5.08 * out[0], 4), round(at[1] + 5.08 * out[1], 4))
                self.wire(at, end)
                self.label(net, end, "left" if out[0] < 0 else "right")
                if net in FLAGGED_NETS:
                    self.flag(end)  # on the stub's end: KiCad joins pins at wire ends only

    # --- output --------------------------------------------------------------------

    def write(self, path):
        junctions = [["junction", ["at", fmt(p[0]), fmt(p[1])], ["diameter", "0"], ["color", "0", "0", "0", "0"],
                      ["uuid", self._uuid("junction", p)]] for p, n in sorted(self.points.items()) if n >= 3]
        paper = next((p for p in PAPERS if self.extent[0] < p[1] - 10 and self.extent[1] < p[2] - 40), PAPERS[-1])
        tree = (["kicad_sch", ["version", "20230409"], ["generator", Str("midi-source")], ["uuid", self.uuid],
                 ["paper", Str(paper[0])],
                 ["title_block", ["title", Str(self.title)], ["date", Str(date.today().isoformat())],
                  ["comment", "1", Str("Generated by hardware/blocks; edit the Python, not this file")]],
                 ["lib_symbols"] + [lib_symbol(*v) for v in self.lib_ids.values()]]
                + self.items + junctions
                + [["sheet_instances", ["path", Str("/"), ["page", Str("1")]]]])
        with open(path, "w") as f:
            f.write(dumps(tree) + "\n")


def layout(sheet, pico, blocks, top=20.32):
    """The Pico on the left, the blocks in columns to its right."""
    sheet.draw_pico(pico, 50.8, top + 43.18)
    x, y, column_width = 116.84, top, 0
    for b in blocks:
        if y > top and y + b.height > 170:
            x, y, column_width = x + column_width + 10.16, top, 0
        sheet.frame(x, y, b.width, b.height, b.title)
        b.draw(sheet, x, y)
        y += b.height + 7.62
        column_width = max(column_width, b.width)
