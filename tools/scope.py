#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["PySide6-Essentials>=6.7", "pyqtgraph>=0.13.7", "numpy>=1.26"]
# ///
"""midi-source scope: the board's analog inputs and its MIDI, live, on one time axis.

    uv run tools/scope.py                (uv installs the libraries on first run)
    uv run tools/scope.py --port /dev/cu.usbmodem21103

It opens the board's console and starts the live stream (console V). Each
analog input gets a plot of every millisecond: the band from its lowest to its
highest sample, and its mean; pads also show their threshold and the peak that
gives velocity 127, and a dot on each hit's peak. Below, the MIDI the
instrument sends: each note a stem at its velocity with a bar for as long as it
lasts, each CC a stepped line.

The Pad tab shows a pad read through three piezos from above: where each
strike landed, and the zone pad's zones. Calibrate there walks you through the
calibration spots (a ring shows where to hit), learns where strikes land from
the board's own levels (tools/padmap.py), compares it with the board's current
calibration, and saves it for the firmware (src/pad_calibration.h).

Build & Flash builds the firmware for the board the stream reports, flashes it
(tools/build.sh, tools/flash.sh) and reconnects. Pause, then drag to pan and
scroll to zoom. macOS only for now (boards.py finds the board through ioreg).

The stream is read on its own thread, so slow drawing lowers the frame rate
but never makes the display fall behind.
"""

import argparse
import bisect
import json
import math
import os
import re
import select
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
from boards import MIDI_SOURCE, usb_devices  # noqa: E402
from console import open_port  # noqa: E402
import padmap  # noqa: E402

HISTORY_S = 120   # seconds of data kept
BOARDS = ["pico", "pico_w", "pico2", "pico2_w"]
GREETING = re.compile(r"midi-source on (\w+)\.")  # the console's banner, in every build
WINDOWS = [1, 2, 5, 10, 30]
BAND = (90, 170, 255)


def pin_name(adc):
    """The board's analog input number as a pin: 0-2 are GP26-GP28, 8-15 the MCP3008's CH0-CH7."""
    return f"CH{adc - 8}" if adc >= 8 else f"GP{26 + adc}"


class Ring:
    """Fixed-size rows of floats; the oldest rows are overwritten."""

    def __init__(self, columns, size):
        self.data = np.zeros((size, columns))
        self.size, self.n, self.head = size, 0, 0

    def extend(self, rows):
        rows = np.asarray(rows, dtype=float)[-self.size:]
        k = len(rows)
        end = self.head + k
        if end <= self.size:
            self.data[self.head:end] = rows
        else:
            split = self.size - self.head
            self.data[self.head:] = rows[:split]
            self.data[:k - split] = rows[split:]
        self.head = end % self.size
        self.n = min(self.size, self.n + k)

    def tail(self, k):
        """A copy of the newest k rows, oldest first."""
        k = min(k, self.n)
        start = (self.head - k) % self.size
        if start + k <= self.size:
            return self.data[start:start + k].copy()
        return np.concatenate((self.data[start:], self.data[:self.head]))

    def clear(self):
        self.n = self.head = 0


class Stream:
    """The parsed stream. The reader thread writes it; the window takes
    snapshots under `lock`."""

    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.header_done = False
        self.board, self.rate = None, 0
        self.mcp3008 = None  # (samples per second per channel, bits), from "H mcp3008"
        self.inputs, self.pots, self.pads = [], {}, {}
        self.analog = None
        self.notes = []      # [t_on, t_off or None, channel, note, velocity], by t_on
        self.held = {}       # (channel, note) -> index in notes
        self.ccs = {}        # (channel, cc) -> ([t], [value])
        self.hits = []       # (t, adc, peak, velocity)
        self.zones = None    # the board's zone pad, from "H zones"
        self.calibration_run = None  # the calibration its map came from, from "H calibration"
        self.strikes = []    # its located strikes, from "L" lines
        self.strike_total = 0  # strikes ever parsed (the list is trimmed)
        self._wrap, self._last = 0, None
        self.version = 0     # bumped on every change, so the window redraws only when needed

    def _time(self, raw):
        """Board microseconds (wrapping at 2^32) to seconds."""
        if self._last is not None and raw < self._last - (1 << 31):
            self._wrap += 1 << 32
        self._last = raw
        return (raw + self._wrap) / 1e6

    def now(self):
        return (self._last + self._wrap) / 1e6 if self._last is not None else 0.0

    def feed_lines(self, lines):
        """Parse a batch; returns the lines that weren't part of the stream."""
        other, frames = [], []
        width = 1 + 3 * len(self.inputs)
        for line in lines:
            parts = line.split()
            if not parts:
                continue
            kind = parts[0]
            try:
                if kind == "A":
                    if self.analog is not None and len(parts) == width + 1:
                        values = list(map(int, parts[1:]))
                        values[0] = self._time(values[0])
                        frames.append(values)
                elif kind == "M":
                    self._midi(self._time(int(parts[1])), int(parts[2]), int(parts[3]), int(parts[4]))
                elif kind == "P":
                    self.hits.append((self._time(int(parts[1])), int(parts[2]), int(parts[3]), int(parts[4])))
                elif kind == "L":
                    v = [int(x) for x in parts[1:]]
                    self.strikes.append({"t": self._time(v[0]), "x": v[1] / 10, "y": v[2] / 10, "zone": v[3],
                                         "velocity": v[4], "levels": v[5:]})
                    self.strike_total += 1
                elif kind == "H":
                    self._header(parts[1], parts[2:])
                    width = 1 + 3 * len(self.inputs)
                else:
                    other.append(line)
            except (ValueError, IndexError):
                other.append(line)
        if frames:
            self.analog.extend(frames)
        if frames or len(other) < len(lines):
            self.version += 1
        return other

    def _header(self, key, values):
        if key == "board":
            self.board = values[0]
        elif key == "rate":
            self.rate = int(values[0])
        elif key == "inputs":
            self.inputs = [int(v) for v in values]
        elif key == "pot":
            adc, cc, ch = map(int, values)
            self.pots[adc] = (cc, ch)
        elif key == "pad":  # threshold and peak count from the pad's resting level
            adc, note, ch, threshold, peak, *biased = map(int, values)
            self.pads[adc] = (note, ch, threshold, peak, bool(biased and biased[0]))
        elif key == "mcp3008":
            self.mcp3008 = (int(values[0]), int(values[1]))
        elif key == "zones":  # ... locate <input>... extra <input>...
            k, e = values.index("locate"), values.index("extra")
            v = [int(x) for x in values[:k]]
            locate, extra = [int(x) for x in values[k + 1:e]], [int(x) for x in values[e + 1:]]
            self.zones = {"split": (v[0] / 10, v[1] / 10), "notes": v[2:6], "channel": v[6], "threshold": v[7],
                          "max": v[8], "locate": locate, "extra": extra, "adc": locate + extra}
        elif key == "calibration":
            self.calibration_run = values[0]
        elif key == "end":
            self.analog = Ring(1 + 3 * len(self.inputs), HISTORY_S * 1000)
            self.header_done = True

    def _midi(self, t, status, d1, d2):
        kind, ch = status & 0xF0, (status & 0x0F) + 1
        if kind == 0x90 and d2 > 0:
            self.held[(ch, d1)] = len(self.notes)
            self.notes.append([t, None, ch, d1, d2])
        elif kind == 0x80 or kind == 0x90:
            i = self.held.pop((ch, d1), None)
            if i is not None:
                self.notes[i][1] = t
        elif kind == 0xB0:
            ts, vs = self.ccs.setdefault((ch, d1), ([], []))
            ts.append(t)
            vs.append(d2)

    def trim(self):
        """Forget MIDI older than the history (the analog ring trims itself)."""
        cutoff = self.now() - HISTORY_S
        if self.notes and self.notes[0][0] < cutoff - 10:
            keep = next((i for i, n in enumerate(self.notes) if n[0] >= cutoff), len(self.notes))
            self.notes = self.notes[keep:]
            self.held = {k: i - keep for k, i in self.held.items() if i >= keep}
        for key, (ts, vs) in self.ccs.items():
            k = bisect.bisect_left(ts, cutoff)
            if k > 1000:
                self.ccs[key] = (ts[k - 1:], vs[k - 1:])  # keep the value in force at the cutoff
        if self.hits and self.hits[0][0] < cutoff - 10:
            self.hits = [h for h in self.hits if h[0] >= cutoff]
        if len(self.strikes) > 500:
            self.strikes = self.strikes[-200:]

    def snapshot(self, window_s):
        """What the window needs to draw [now - window_s, now], copied."""
        with self.lock:
            now = self.now()
            start = now - window_s
            rows = self.analog.tail(int(window_s * 1000) + 50) if self.analog else np.zeros((0, 1))
            i = bisect.bisect_left(self.notes, start - 5, key=lambda n: n[0])
            notes = [list(n) for n in self.notes[i:] if n[1] is None or n[1] >= start]
            ccs = {}
            for key, (ts, vs) in self.ccs.items():
                k = max(0, bisect.bisect_left(ts, start) - 1)
                if k < len(ts):
                    ccs[key] = (ts[k:], vs[k:])
            j = bisect.bisect_left(self.hits, start, key=lambda h: h[0])
            hits = self.hits[j:]
        return now, start, rows, notes, ccs, hits


class Reader(threading.Thread):
    """Owns the console port: connects, reads and parses, reconnects."""

    def __init__(self, stream, port=None):
        super().__init__(daemon=True)
        self.stream, self.fixed_port = stream, port
        self.fd = None
        self.port = self.serial = None
        self.messages = []                # for the window's log
        self.greeting_board = None        # from the console banner, even without the stream
        self.paused = threading.Event()   # set while flashing
        self.stop = threading.Event()

    def say(self, text):
        self.messages.append(text)

    def _connect(self):
        boards = [d for d in usb_devices() if (d["vid"], d["pid"]) == MIDI_SOURCE and d["ports"]]
        if self.fixed_port:
            boards = [d for d in boards if self.fixed_port in d["ports"]] or \
                     [{"ports": [self.fixed_port], "serial": None}]
        for board in boards[:1]:
            try:
                self.fd = open_port(board["ports"][0])
            except OSError:
                return
            self.port, self.serial = board["ports"][0], board["serial"]
            self.greeting_board = None
            with self.stream.lock:
                self.stream.reset()
            os.write(self.fd, b"V")
            self.say(f"connected to {self.port}")

    def _close(self):
        if self.fd is not None:
            try:
                os.write(self.fd, b"v")
                os.close(self.fd)
            except OSError:
                pass
        self.fd = None

    def run(self):
        partial = b""
        while not self.stop.is_set():
            if self.paused.is_set():
                self._close()
                time.sleep(0.1)
                continue
            if self.fd is None:
                self._connect()
                if self.fd is None:
                    time.sleep(1)
                continue
            try:
                if not select.select([self.fd], [], [], 0.05)[0]:
                    continue
                chunks = [os.read(self.fd, 65536)]
                while select.select([self.fd], [], [], 0)[0] and len(chunks) < 64:
                    chunks.append(os.read(self.fd, 65536))
                if not chunks[-1]:
                    raise OSError("port closed")
            except OSError:
                self._close()
                self.say("board disconnected")
                continue
            data = partial + b"".join(chunks)
            *lines, partial = data.split(b"\n")
            text = [l.decode(errors="replace").rstrip("\r") for l in lines]
            with self.stream.lock:
                was_done = self.stream.header_done
                other = self.stream.feed_lines(text)
                self.stream.trim()
                if self.stream.header_done and not was_done:
                    s = self.stream
                    self.say(f"streaming from {s.board}: inputs {', '.join(pin_name(a) for a in s.inputs)}")
            for line in other:
                m = GREETING.search(line)
                if m:
                    self.greeting_board = m.group(1)
                self.say(line)
        self._close()


def decimate(t, columns, buckets):
    """Per bucket of rows: first time, lowest lo, highest hi, mean of means.
    About one bucket per pixel keeps drawing cheap at any window length."""
    n = len(t)
    if n <= buckets * 2:
        return t, [c for _, c in columns]
    size = n // buckets
    off = n - size * buckets  # drop the oldest few rows so the newest stay aligned
    shape = (buckets, size)
    out = []
    for kind, col in columns:
        c = col[off:].reshape(shape)
        out.append(c.min(axis=1) if kind == "lo" else c.max(axis=1) if kind == "hi" else c.mean(axis=1))
    return t[off:].reshape(shape)[:, 0], out


def color(i):
    return pg.intColor(i, hues=8, values=1, maxValue=255, minValue=150, sat=200)


# --- locating strikes on a pad with several piezos, and calibrating them ------------------

class Locator:
    """Turns the stream's pad hits into located strikes, with the calibration's
    map from signature (each piezo's signed level, over their length) to place."""

    GROUP_S = 0.030                   # hit events this close together are one strike
    BEFORE_S, AFTER_S = 0.004, 0.0015  # the strike's frames, around its first hit event
    REST_S = (0.060, 0.008)           # each piezo's resting level: 60 ms to 8 ms before

    def __init__(self, cal):
        self.w = np.array(cal["signature_map"])
        self.order = padmap.signature_pins(cal)  # the pins the map reads, in order
        self.done_until = -1e9        # strikes up to this board time are handled
        self.strikes = []

    def update(self, stream):
        """Locate any new strikes; True if there were some."""
        with stream.lock:
            now = stream.now()
            if now < self.done_until - 1:  # the board restarted: its clock did too
                self.done_until = -1e9
            hits = [h for h in stream.hits if h[0] > self.done_until]
            if not hits or stream.analog is None:
                return False
            rows = stream.analog.tail(4000)
            inputs = list(stream.inputs)
        added, i = False, 0
        while i < len(hits):
            t0 = hits[i][0]
            if now < t0 + self.GROUP_S:
                break  # more of this strike's hit events may still come
            group = [h for h in hits[i:] if h[0] < t0 + self.GROUP_S]
            i += len(group)
            self.done_until = t0 + self.GROUP_S
            strike = self.measure(rows, inputs, t0, max(h[3] for h in group))
            if strike:
                self.strikes.append(strike)
                added = True
        self.strikes = self.strikes[-200:]
        return added

    def measure(self, rows, inputs, t0, velocity):
        t = rows[:, 0]
        window = rows[(t >= t0 - self.BEFORE_S) & (t <= t0 + self.AFTER_S)]
        quiet = rows[(t >= t0 - self.REST_S[0]) & (t <= t0 - self.REST_S[1])]
        if not len(window) or not len(quiet):
            return None
        levels = {}
        for k, adc in enumerate(inputs):
            pin = pin_name(adc)
            if pin in self.order:
                rest = float(np.median(quiet[:, 3 + 3 * k]))
                up = float(window[:, 2 + 3 * k].max()) - rest
                down = rest - float(window[:, 1 + 3 * k].min())
                levels[pin] = up if up >= down else -down
        if set(levels) != set(self.order):
            return None
        v = np.array([levels[p] for p in self.order])
        n = np.linalg.norm(v)
        if n == 0:
            return None
        x, y = np.append(v / n, 1.0) @ self.w
        return {"t": t0, "x": float(x), "y": float(y), "velocity": velocity, "levels": levels}


ZONE_NAMES = ["top-left", "top-right", "bottom-left", "bottom-right"]
ZONE_COLORS = [(90, 170, 255), (120, 220, 120), (230, 120, 220), (255, 150, 80)]


class PadView(pg.PlotWidget):
    """The pad seen from above: the piezos and the calibration spots, the zones
    if the board has a zone pad, where the latest strikes landed (the newest
    large and yellow, older ones fading, in their zone's colour), and a
    calibration while one is under way."""

    SHOWN = 30
    TARGET = (0, 220, 255)

    def __init__(self):
        super().__init__()
        self.setAspectLocked(True)
        self.showGrid(x=True, y=True, alpha=0.15)
        self.setLabel("bottom", "cm")
        self.setLabel("left", "cm")
        self.setXRange(-9, 9)
        self.setYRange(-12, 3.5)
        self.dots = pg.ScatterPlotItem(pen=None)
        self.dots.setZValue(10)
        self.addItem(self.dots)
        self.marks = pg.ScatterPlotItem()  # a calibration's spots: done, to do
        self.marks.setZValue(15)
        self.addItem(self.marks)
        self.target = pg.ScatterPlotItem(symbol="o", brush=None)
        self.target.setZValue(20)
        self.addItem(self.target)
        self.count = pg.TextItem(color=self.TARGET, anchor=(0, 1))
        self.count.setZValue(20)
        self.addItem(self.count)
        self.geometry_items, self.zone_items, self.result_items = [], [], []
        self.piezo_labels = []  # hidden under a finished calibration's labels
        self.zones = None

    def _add(self, items, item):
        self.addItem(item)
        items.append(item)

    def _remove(self, items):
        for item in items:
            self.removeItem(item)
        items.clear()

    def set_geometry(self, cal):
        """The piezos (with their pins once calibrated) and the calibration spots."""
        self._remove(self.geometry_items)
        piezos = {name: tuple(pos) for name, pos in (cal["piezos_cm"] if cal else padmap.PIEZOS).items()}
        pins = {}  # piezo -> its pins, the Pico ADC's first
        for pin, piezo in sorted((cal or {}).get("pins", {}).items(), key=lambda kv: (kv[0][:2] != "GP", kv[0])):
            pins[piezo] = f"{pins[piezo]} {pin}" if piezo in pins else pin
        spots = cal.get("spots_cm", {}) if cal else {name: pos for name, _, pos in padmap.SPOTS}
        corners = list(piezos.values())
        self._add(self.geometry_items, pg.PlotDataItem(
            [p[0] for p in corners + corners[:1]], [p[1] for p in corners + corners[:1]],
            pen=pg.mkPen((120, 120, 120), width=1, style=QtCore.Qt.PenStyle.DashLine)))
        self._add(self.geometry_items, pg.ScatterPlotItem(
            [p[0] for p in corners], [p[1] for p in corners], size=22, brush=pg.mkBrush(40, 40, 40),
            pen=pg.mkPen((220, 220, 220), width=2)))
        for name, (x, y) in piezos.items():
            label = pg.TextItem(f"{name}\n{pins.get(name, '?')}", color=(200, 200, 200), anchor=(0.5, -0.4))
            label.setPos(x, y)
            self._add(self.geometry_items, label)
        self.piezo_labels = [item for item in self.geometry_items if isinstance(item, pg.TextItem)]
        self._add(self.geometry_items, pg.ScatterPlotItem(
            [p[0] for p in spots.values()], [p[1] for p in spots.values()], symbol="+", size=10,
            pen=pg.mkPen((110, 110, 110)), brush=None))

    def set_zones(self, zones):
        """Draw the board's zone split and each zone's note; None removes them."""
        self._remove(self.zone_items)
        self.zones = zones
        if not zones:
            return
        sx, sy = zones["split"]
        pen = pg.mkPen((200, 200, 120), width=1, style=QtCore.Qt.PenStyle.DashLine)
        for item in (pg.InfiniteLine(pos=sx, angle=90, pen=pen), pg.InfiniteLine(pos=sy, angle=0, pen=pen)):
            self._add(self.zone_items, item)
        corners = [(-1, 1), (1, 1), (-1, -1), (1, -1)]
        for zone, (cx, cy) in enumerate(corners):
            label = pg.TextItem(f"{ZONE_NAMES[zone]}\nnote {zones['notes'][zone]}", color=ZONE_COLORS[zone],
                                anchor=(0.5, 0.5))
            label.setPos(sx + cx * 7.5, sy + cy * 6.5)
            self._add(self.zone_items, label)

    def show_strikes(self, strikes, names=()):
        """Draw the latest strikes; returns a line about the newest (its
        levels named by `names` when they come as a list)."""
        shown = strikes[-self.SHOWN:]
        spots = []
        for age, s in enumerate(reversed(shown)):
            newest = age == 0
            alpha = 255 if newest else max(40, 200 - age * 6)
            base = ZONE_COLORS[s["zone"]] if "zone" in s else (90, 170, 255)
            spots.append({"pos": (s["x"], s["y"]), "size": 8 + 18 * s["velocity"] / 127,
                          "brush": pg.mkBrush(255, 220, 60, 255) if newest else pg.mkBrush(*base, alpha)})
        self.dots.setData(list(reversed(spots)))
        if not shown:
            return ""
        s = shown[-1]
        lv = s["levels"] if isinstance(s["levels"], dict) else dict(zip(names, s["levels"]))
        levels = "  ".join(f"{p} {v:+5.0f}" for p, v in lv.items())
        where = ""
        if "zone" in s and self.zones:
            where = f" -> {ZONE_NAMES[s['zone']]}, note {self.zones['notes'][s['zone']]}"
        return (f"last strike: x {s['x']:+5.1f} cm, y {s['y']:+5.1f} cm{where}   velocity {s['velocity']:3d}   "
                f"levels: {levels}")

    def show_session(self, session, flash):
        """A calibration under way: the spots done (green), to do (grey) and the
        ring on the one to hit now, pulsing, brighter for a moment (`flash`)
        after a strike counted."""
        marks = []
        for i, (_, _, pos) in enumerate(padmap.SPOTS):
            if i < session.spot:
                marks.append({"pos": pos, "symbol": "o", "size": 12, "pen": None, "brush": pg.mkBrush(90, 220, 110)})
            elif i > session.spot:
                marks.append({"pos": pos, "symbol": "+", "size": 16, "pen": pg.mkPen((170, 170, 170), width=2),
                              "brush": None})
        self.marks.setData(marks)
        if session.done:
            self.target.clear()
            self.count.setText("")
            return
        x, y = padmap.SPOTS[session.spot][2]
        pulse = 0.5 + 0.5 * math.sin(time.monotonic() * 2 * math.pi * 1.2)
        size = 30 + 10 * pulse + 18 * flash
        width = 3 + 3 * flash
        self.target.setData([x], [y], size=size, pen=pg.mkPen(self.TARGET + (int(170 + 85 * max(pulse, flash)),),
                                                                width=width))
        self.count.setText(f"{len(session.hits[session.spot])}/{session.per_spot}")
        self.count.setPos(x + 0.9, y + 0.9)

    def show_result(self, session):
        """A finished calibration: every strike where the new map puts it,
        joined to its spot, and a ring where the board put it."""
        self.clear_session()
        self.dots.setVisible(False)
        for label in self.piezo_labels:
            label.setVisible(False)
        spots = session.ev["spots"]
        cx = sum(spot["pos"][0] for spot in spots) / len(spots)
        cy = sum(spot["pos"][1] for spot in spots) / len(spots)
        for i, (spot, hits) in enumerate(zip(spots, session.hits)):
            c = color(i)
            (sx, sy), fitted = spot["pos"], spot["fitted"]
            self._add(self.result_items, pg.PlotDataItem(
                [v for x, _ in fitted for v in (sx, x)], [v for _, y in fitted for v in (sy, y)],
                connect="pairs", pen=pg.mkPen(c, width=1)))
            self._add(self.result_items, pg.ScatterPlotItem(
                [p[0] for p in fitted], [p[1] for p in fitted], size=8, pen=None, brush=pg.mkBrush(c)))
            self._add(self.result_items, pg.ScatterPlotItem(
                [h["board_cm"][0] for h in hits], [h["board_cm"][1] for h in hits], size=9, brush=None,
                pen=pg.mkPen((150, 150, 150), width=1)))
            self._add(self.result_items, pg.ScatterPlotItem([sx], [sy], symbol="+", size=18, brush=None,
                                                            pen=pg.mkPen((255, 255, 255), width=2)))
            # Each spot's label away from the middle of the spots, so they don't overlap.
            dx, dy = sx - cx, sy - cy
            n = math.hypot(dx, dy)
            ux, uy = (dx / n, dy / n) if n > 0.5 else (-1.0, 0.0)  # the centre: to its left
            label = pg.TextItem(f"{spot['name']} {spot['fitted_error']:.1f} cm", color=c,
                                anchor=(0.5 - 0.5 * ux, 0.5 + 0.5 * uy))
            label.setPos(sx + 1.2 * ux, sy + 1.2 * uy)
            self._add(self.result_items, label)
        for item in self.result_items:
            item.setZValue(15)

    def clear_session(self):
        self.marks.clear()
        self.target.clear()
        self.count.setText("")
        self._remove(self.result_items)
        self.dots.setVisible(True)
        for label in self.piezo_labels:
            label.setVisible(True)


class Calibration:
    """An interactive calibration: padmap.SPOTS in turn, a few strikes on each,
    as the board measured them (its L lines' levels, in the zone pad's input
    order): the same levels the firmware will locate strikes from. When the
    zone pad has extra inputs (the same piezos on an MCP3008), a map is made
    from each ADC's pins and from both, to compare."""

    GAP_S = 0.12   # a strike this soon after the last is its bounce (or a flam): skipped
    PAUSE_S = 0.8  # strikes this soon after a spot is done are skipped: time to move the stick

    def __init__(self, pins, locate_pins, per_spot):
        self.pins, self.locate_pins, self.per_spot = pins, locate_pins, per_spot
        self.hits = [[] for _ in padmap.SPOTS]  # per spot: {"levels": {pin: level}, "board_cm": (x, y), "velocity"}
        self.spot = 0
        self.skip_until = float("-inf")  # board time
        self.problem = None  # why the spot just done has to be done again
        self.maps = None     # once finished: [(label, calibration, evaluation)]
        self.notes = []      # inputs left out of the maps, and why
        self.choice = 0      # the map shown and saved

    @property
    def cal(self):
        return self.maps[self.choice][1] if self.maps else None

    @property
    def ev(self):
        return self.maps[self.choice][2] if self.maps else None

    @property
    def done(self):
        return self.spot == len(padmap.SPOTS)

    def add(self, strike):
        """One of the stream's strikes; True if it counted."""
        if self.done or strike["t"] < self.skip_until:
            return False
        self.skip_until = strike["t"] + self.GAP_S
        hits = self.hits[self.spot]
        hits.append({"levels": dict(zip(self.pins, strike["levels"])), "board_cm": (strike["x"], strike["y"]),
                     "velocity": strike["velocity"]})
        self.problem = None
        if len(hits) == self.per_spot:
            self.skip_until = strike["t"] + self.PAUSE_S
            self.problem = self.corner_problem()
            if self.problem:
                hits.clear()
            else:
                self.spot += 1
                if self.done:
                    self.finish()
        return True

    def corner_problem(self):
        """Each spot above a piezo must have its own loudest pin, among those
        the board locates with: it tells which pin is which."""
        if self.spot >= padmap.CORNERS:
            return None

        def loudest(i):
            return padmap.loudest_pin([{p: h["levels"][p] for p in self.locate_pins} for h in self.hits[i]])

        pin = loudest(self.spot)
        for i in range(self.spot):
            if loudest(i) == pin:
                return (f"{pin} was the loudest here, as it was at {padmap.SPOTS[i][0]}: "
                        f"hit {padmap.SPOTS[self.spot][1]}, and do this spot again.")
        return None

    def undo(self):
        """Forget the last strike that counted (going back a spot if need be)."""
        self.problem = None
        self.maps = None
        if not self.done and self.hits[self.spot]:
            self.hits[self.spot].pop()
        elif self.spot > 0:
            self.spot -= 1
            self.hits[self.spot].pop()

    def finish(self):
        spots_hits = [(spot, [h["levels"] for h in hits]) for spot, hits in zip(padmap.SPOTS, self.hits)]
        try:
            self.notes, maps = padmap.calibrate_all(spots_hits, "board strikes (tools/scope.py)")
        except ValueError as e:  # no ADC's pins matched the piezos: start over
            self.hits = [[] for _ in padmap.SPOTS]
            self.spot, self.problem = 0, f"{e}. Let's start again."
            return
        board = [math.dist(h["board_cm"], pos) for (_, _, pos), hits in zip(padmap.SPOTS, self.hits) for h in hits]
        self.board = padmap.summary(board)
        for _, cal, _ in maps:
            cal["hits_per_spot"] = self.per_spot
        self.maps, self.choice = maps, 0

    def save(self):
        """The run's hits.json and calibration.json, and src/pad_calibration.h."""
        out = padmap.RUNS / time.strftime("%Y%m%d-%H%M%S")
        out.mkdir(parents=True, exist_ok=True)
        spots = [{"name": name, "pos": pos, "hits": hits} for (name, _, pos), hits in zip(padmap.SPOTS, self.hits)]
        (out / "hits.json").write_text(json.dumps({"source": self.cal["source"], "pins": self.pins, "spots": spots},
                                                  indent=1))
        header = padmap.save(out, self.cal)
        return out / "calibration.json", header


class PadTab(QtWidgets.QWidget):
    """The Pad tab: the PadView, the calibration it shows, and calibrating."""

    flash_requested = QtCore.Signal()
    said = QtCore.Signal(str)
    FLASH_S = 0.25

    def __init__(self):
        super().__init__()
        box = QtWidgets.QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        top = QtWidgets.QHBoxLayout()
        top.setContentsMargins(8, 4, 8, 0)
        self.info = QtWidgets.QLabel()
        self.info.setWordWrap(True)
        self.per_spot = QtWidgets.QSpinBox()
        self.per_spot.setRange(2, 10)
        self.per_spot.setValue(4)
        self.per_spot.setSuffix(" strikes per spot")
        self.calibrate = QtWidgets.QPushButton("Calibrate…")
        self.calibrate.clicked.connect(self.start)
        top.addWidget(self.info, 1)
        top.addWidget(self.per_spot)
        top.addWidget(self.calibrate)

        self.panel = QtWidgets.QFrame()
        self.panel.setObjectName("panel")
        self.panel.setStyleSheet("#panel { background: rgba(0, 170, 220, 38); border-radius: 6px; }")
        row = QtWidgets.QHBoxLayout(self.panel)
        self.prompt = QtWidgets.QLabel()
        self.prompt.setWordWrap(True)
        self.prompt.setTextFormat(QtCore.Qt.TextFormat.RichText)
        font = self.prompt.font()
        font.setPointSize(font.pointSize() + 3)
        self.prompt.setFont(font)
        row.addWidget(self.prompt, 1)
        self.choose = QtWidgets.QComboBox()
        self.choose.setToolTip("The map shown, and the one Save writes for the firmware")
        self.choose.currentIndexChanged.connect(self.chose)
        self.choose.hide()
        row.addWidget(self.choose)
        self.buttons = {}
        for key, text, action in (("undo", "Undo strike", self.undo), ("cancel", "Cancel", self.stop),
                                  ("save", "Save", self.save), ("flash", "Save, Build && Flash", self.save_and_flash),
                                  ("again", "Start again", self.start), ("discard", "Discard", self.stop)):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(action)
            row.addWidget(b)
            self.buttons[key] = b
        self.panel.hide()

        self.view = PadView()
        self.last = QtWidgets.QLabel()
        self.last.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont))
        self.last.setContentsMargins(8, 4, 8, 4)
        box.addLayout(top)
        box.addWidget(self.panel)
        box.addWidget(self.view, 1)
        box.addWidget(self.last)
        for key, action in ((QtCore.Qt.Key.Key_Backspace, self.undo), (QtCore.Qt.Key.Key_Escape, self.stop)):
            shortcut = QtGui.QShortcut(QtGui.QKeySequence(key), self)
            shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(action)

        self.session = None
        self.flash_until = 0.0
        self.zones = self.pins = self.locate_pins = None
        self.board_run = None
        self.seen = 0          # the stream's strikes taken so far (its strike_total)
        self.redraw = False    # strikes came in while paused
        self.cal_path, self.cal = padmap.newest()
        self.locator = Locator(self.cal) if self.cal else None
        self.enable()
        self.show_calibration()

    # --- what's shown -----------------------------------------------------------------

    def show_calibration(self):
        self.view.set_geometry(self.cal)
        if not self.cal:
            text = "No calibration yet: press Calibrate."
        else:
            run, err = self.cal_path.parent.name, self.cal.get("error_cm", {})
            text = (f"Calibration {run}: between its spots (+) strikes land within about {err.get('median', 0):.1f} "
                    f"cm, at worst {err.get('worst', 0):.1f} cm. Dot size follows velocity.")
            if self.board_run and self.board_run != run:
                text += (f"<br><b>The board still locates strikes with calibration {self.board_run}:</b> "
                         f"Build &amp; Flash to put this one in.")
        self.info.setText(text)

    def enable(self):
        idle = self.session is None
        self.calibrate.setEnabled(idle and self.zones is not None)
        self.per_spot.setEnabled(idle)
        self.calibrate.setToolTip("Hit the spots the Pad tab shows, a few strikes each; it learns where strikes land "
                                  "from the board's own levels" if self.zones is not None else
                                  "Needs a board with a zone pad in its layout (src/layout.c); without one, "
                                  "uv run tools/calibrate.py works from raw captures")

    def show_panel(self):
        s = self.session
        self.panel.setVisible(s is not None)
        if s is None:
            return
        finished = s.maps is not None
        for key, b in self.buttons.items():
            b.setVisible((key in ("save", "flash", "again", "discard")) == finished)
        self.buttons["undo"].setEnabled(s.spot > 0 or bool(s.hits[0]))
        self.choose.setVisible(finished and len(s.maps) > 1)
        if finished:
            self.prompt.setText(self.result_text(s))
            return
        name, where, _ = padmap.SPOTS[s.spot]
        got = len(s.hits[s.spot])
        text = (f"<b>Spot {s.spot + 1} of {len(padmap.SPOTS)}:</b> {where}. Hit it {s.per_spot} times, medium "
                f"strength, about a second apart. &nbsp;{'●' * got}{'○' * (s.per_spot - got)}")
        if s.problem:
            text += f"<br><span style='color:#ff8080'>{s.problem}</span>"
        elif s.spot < padmap.CORNERS:
            text += ("<br><small>The first three spots are right above each piezo: they tell which pin is which. "
                     "Backspace undoes a strike, Escape cancels.</small>")
        self.prompt.setText(text)

    def result_text(self, s):
        pins = ", ".join(f"{piezo} " + " / ".join(sorted((pin for pin, pz in s.cal["pins"].items() if pz == piezo),
                                                       key=lambda pin: (pin[:2] != "GP", pin)))
                         for piezo in padmap.ORDER)
        text = f"<b>Calibrated</b> from {sum(len(h) for h in s.hits)} strikes; pins: {pins}.<br>"
        if len(s.maps) == 1:
            at, between, board = s.ev["at"], s.ev["between"], s.board
            text += (f"These strikes land within <b>{at['median']:.1f} cm</b> of their spot with the new map (median; "
                     f"{at['worst']:.1f} at worst), {board['median']:.1f} cm ({board['worst']:.1f}) with the "
                     f"board's current one. Between spots expect about <b>{between['median']:.1f} cm</b> "
                     f"({between['worst']:.1f}): each spot located by a map made without it.")
        else:
            rows = "".join(
                f"<tr><td>{'▸ ' if i == s.choice else ''}{label}</td><td>{ev['at']['median']:.1f} "
                f"({ev['at']['worst']:.1f})</td><td>{ev['between']['median']:.1f} ({ev['between']['worst']:.1f})</td></tr>"
                for i, (label, _, ev) in enumerate(s.maps))
            text += ("<table cellspacing=0 cellpadding=1><tr><td><i>map from</i></td><td><i>at the spots, cm</i>"
                     "&nbsp;&nbsp;</td><td><i>between spots</i></td></tr>" + rows +
                     f"<tr><td>the board's current map</td><td>{s.board['median']:.1f} ({s.board['worst']:.1f})"
                     f"</td><td></td></tr></table>")
        for note in s.notes:
            text += f"<br><span style='color:#ffb060'>{note}</span>"
        return text + ("<br><small>Median (worst). Dots: where the map puts each strike; rings: where the board "
                       "put it. Save writes src/pad_calibration.h; the board uses it once built and flashed.</small>")

    # --- every frame ------------------------------------------------------------------

    def update(self, stream, paused):
        with stream.lock:
            zones, total, run = stream.zones, stream.strike_total, stream.calibration_run
            if total < self.seen:  # the stream started again (and the board's clock with it, maybe)
                self.seen = 0
                if self.session:
                    self.session.skip_until = float("-inf")
            fresh = stream.strikes[len(stream.strikes) - min(total - self.seen, len(stream.strikes)):] \
                if total > self.seen else []
            if fresh or self.redraw:
                strikes = list(stream.strikes)
            self.seen = total
        if zones is not self.zones:
            self.zones = zones
            self.pins = [pin_name(a) for a in zones["adc"]] if zones else None
            self.locate_pins = [pin_name(a) for a in zones["locate"]] if zones else None
            self.view.set_zones(zones)
            self.enable()
        if run != self.board_run:
            self.board_run = run
            self.show_calibration()
        s = self.session
        if s and s.maps is None:
            if any([s.add(strike) for strike in fresh]):
                self.flash_until = time.monotonic() + self.FLASH_S
                if s.maps is not None:
                    self.choose.blockSignals(True)
                    self.choose.clear()
                    self.choose.addItems([label for label, _, _ in s.maps])
                    self.choose.blockSignals(False)
                    self.view.show_result(s)
                self.show_panel()
            if s.maps is None:
                self.view.show_session(s, max(0.0, self.flash_until - time.monotonic()) / self.FLASH_S)
        if zones is not None:
            if fresh or self.redraw:
                if paused:
                    self.redraw = True
                else:
                    self.redraw = False
                    self.last.setText(self.view.show_strikes(strikes, self.pins))
        elif self.locator and not paused and self.locator.update(stream):
            self.last.setText(self.view.show_strikes(self.locator.strikes))

    def clear(self):
        if self.locator:
            self.locator.strikes = []
        self.last.setText(self.view.show_strikes([]))

    # --- calibrating ------------------------------------------------------------------

    def start(self):
        if self.zones is None:
            return
        self.view.clear_session()
        self.session = Calibration(self.pins, self.locate_pins, self.per_spot.value())  # the strikes from now on
        self.enable()
        self.show_panel()
        self.said.emit(f"calibrating: {len(padmap.SPOTS)} spots, {self.session.per_spot} strikes each")

    def undo(self):
        s = self.session
        if s is None:
            return
        if s.maps is not None:
            self.view.clear_session()
        s.undo()
        self.show_panel()

    def chose(self, i):
        s = self.session
        if s is None or s.maps is None or i < 0:
            return
        s.choice = i
        self.view.show_result(s)
        self.show_panel()

    def stop(self):
        if self.session is None:
            return
        self.session = None
        self.view.clear_session()
        self.enable()
        self.show_panel()

    def save(self, flash=False):
        s = self.session
        if s is None or s.maps is None:
            return
        path, header = s.save()
        self.cal_path, self.cal = path, json.loads(path.read_text())
        self.locator = Locator(self.cal)
        self.stop()
        self.show_calibration()
        self.said.emit(f"saved {path.relative_to(padmap.ROOT)} and {header.relative_to(padmap.ROOT)}")
        if flash:
            self.flash_requested.emit()

    def save_and_flash(self):
        self.save(flash=True)


class Scope(QtWidgets.QMainWindow):
    def __init__(self, args):
        super().__init__()
        self.setWindowTitle("midi-source scope")
        self.resize(1200, 800)
        self.stream = Stream()
        self.reader = Reader(self.stream, args.port)
        self.window_s = 5
        self.flashing = None
        self.drawn_version, self.built_for = -1, None
        self.y_top = {}
        self.analog_items, self.midi_series = [], {}

        bar = self.addToolBar("controls")
        bar.setMovable(False)
        self.status = QtWidgets.QLabel("looking for a midi-source board…")
        self.pause = QtWidgets.QPushButton("Pause")
        self.pause.setCheckable(True)
        self.span = QtWidgets.QComboBox()
        self.span.addItems([f"{w} s" for w in WINDOWS])
        self.span.setCurrentIndex(WINDOWS.index(self.window_s))
        self.span.currentIndexChanged.connect(self.set_window)
        clear = QtWidgets.QPushButton("Clear")
        clear.clicked.connect(self.clear)
        self.board = QtWidgets.QComboBox()
        self.board.addItems(BOARDS)
        self.board.setToolTip("The board Build & Flash builds for; set from the board when it says")
        self.flash = QtWidgets.QPushButton("Build && Flash")
        self.flash.clicked.connect(self.build_and_flash)
        bar.addWidget(self.status)
        spacer = QtWidgets.QWidget()
        spacer.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Preferred)
        bar.addWidget(spacer)
        for w in (QtWidgets.QLabel("window "), self.span, self.pause, clear, self.board, self.flash):
            bar.addWidget(w)

        self.plots = pg.GraphicsLayoutWidget()
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont))
        self.pad = PadTab()
        self.pad.said.connect(self.log.appendPlainText)
        self.pad.flash_requested.connect(self.build_and_flash)
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self.plots, "Signals")
        self.tabs.addTab(self.pad, "Pad")
        split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        split.addWidget(self.tabs)
        split.addWidget(self.log)
        split.setSizes([640, 140])
        self.setCentralWidget(split)

        self.frame_times = []
        self.drawer = QtCore.QTimer(self, interval=33, timeout=self.tick)
        self.drawer.start()
        self.reader.start()

    # --- plots ----------------------------------------------------------------------

    def build_plots(self):
        s = self.stream
        self.plots.clear()
        self.analog_items = []
        self.scaled = {}  # inputs whose scale follows the signal around rest: adc -> threshold above rest
        for i, adc in enumerate(s.inputs):
            p = self.plots.addPlot(row=i, col=0)
            p.setMouseEnabled(x=True, y=False)
            p.showGrid(x=True, y=True, alpha=0.15)
            p.hideButtons()
            band = p.plot(pen=pg.mkPen(BAND + (110,), width=1), connect="pairs")
            hi = p.plot(pen=pg.mkPen(BAND, width=1))
            mean = p.plot(pen=pg.mkPen((255, 210, 90), width=1.5))
            peaks = pg.ScatterPlotItem(size=7, brush=pg.mkBrush(255, 255, 255), pen=None)
            p.addItem(peaks)
            gp = pin_name(adc) + (" · MCP3008" if adc >= 8 else "")
            lines = {}
            if adc in s.pads:
                note, ch, threshold, peak, biased = s.pads[adc]
                p.setTitle(f"{gp} · pad{' (biased)' if biased else ''} · note {note} on channel {ch} · dots: hit peaks")
                for value, text in ((threshold, "threshold"), (peak, "velocity 127")):
                    line = pg.InfiniteLine(pos=value, angle=0, movable=False,
                                           pen=pg.mkPen((255, 120, 120), style=QtCore.Qt.PenStyle.DashLine),
                                           label=f"{text}: rest + {value}",
                                           labelOpts={"position": 0.98, "color": (255, 140, 140),
                                                      "anchors": [(1, 1), (1, 1)]})
                    p.addItem(line)
                    lines[value] = line
                mean.setVisible(False)  # a pad is about its peaks
                p.setYRange(0, max(4 * threshold, 600), padding=0.02)
                self.scaled[adc] = threshold
            elif s.zones and adc in s.zones["adc"]:
                threshold, locates = s.zones["threshold"], adc in s.zones["locate"]
                p.setTitle(f"{gp} · zone pad · " + ("locates strikes" if locates else "measured with each strike"))
                if locates:  # a strike starts when one of these rises past the threshold
                    line = pg.InfiniteLine(pos=threshold, angle=0, movable=False,
                                           pen=pg.mkPen((255, 120, 120), style=QtCore.Qt.PenStyle.DashLine),
                                           label=f"threshold: rest + {threshold}",
                                           labelOpts={"position": 0.98, "color": (255, 140, 140),
                                                      "anchors": [(1, 1), (1, 1)]})
                    p.addItem(line)
                    lines[threshold] = line
                mean.setVisible(False)
                self.scaled[adc] = threshold
            else:
                if adc in s.pots:
                    cc, ch = s.pots[adc]
                    p.setTitle(f"{gp} · pot · CC {cc} on channel {ch}")
                else:
                    p.setTitle(gp)
                p.setYRange(0, 4095, padding=0.02)
            self.analog_items.append((adc, p, band, hi, mean, peaks, lines))
        m = self.plots.addPlot(row=len(s.inputs), col=0)
        m.setTitle("MIDI out: notes at their velocity (bars last until the note off), CCs as steps")
        m.setYRange(0, 130, padding=0)
        m.setMouseEnabled(x=True, y=False)
        m.showGrid(x=True, y=True, alpha=0.15)
        m.hideButtons()
        m.setLabel("bottom", "board time", units="s")
        m.addLegend(offset=(-10, 10), labelTextSize="8pt")
        for _, p, *_ in self.analog_items:
            p.setXLink(m)
            p.hideAxis("bottom")  # one time axis, under the MIDI plot: the axes repaint every frame
        self.midi_plot, self.midi_series = m, {}
        self.stems = m.plot(pen=pg.mkPen((200, 200, 200, 150), width=1), connect="pairs")
        self.built_for = id(s.analog)
        self.y_top = {}

    def series(self, key, label):
        if key not in self.midi_series:
            c = color(len(self.midi_series))
            if key[0] == "note":
                bars = self.midi_plot.plot(pen=pg.mkPen(c, width=3), connect="pairs", name=label)
                heads = pg.ScatterPlotItem(size=8, brush=pg.mkBrush(c), pen=None)
                self.midi_plot.addItem(heads)
                self.midi_series[key] = (bars, heads)
            else:
                self.midi_series[key] = (self.midi_plot.plot(pen=pg.mkPen(c, width=2), stepMode="left", name=label),)
        return self.midi_series[key]

    def tick(self):
        while self.reader.messages:
            self.log.appendPlainText(self.reader.messages.pop(0))
        s = self.stream
        if s.header_done and self.built_for != id(s.analog):
            self.build_plots()
        self.update_status()
        self.pad.update(s, self.pause.isChecked())
        if not s.header_done or self.built_for is None or self.pause.isChecked() or s.version == self.drawn_version:
            return
        self.drawn_version = s.version
        started = time.perf_counter()
        self.draw()
        self.frame_times = (self.frame_times + [time.perf_counter() - started])[-30:]

    def draw(self):
        now, start, rows, notes, ccs, hits = self.stream.snapshot(self.window_s)
        if len(rows):
            rows = rows[rows[:, 0] >= start]
        t = rows[:, 0] if len(rows) else np.zeros(0)
        width = max(200, int(self.plots.width()))
        for i, (adc, p, band, hi, mean, peaks, lines) in enumerate(self.analog_items):
            if not len(t):
                continue
            tb, (lo, hh, mm) = decimate(t, [("lo", rows[:, 1 + 3 * i]), ("hi", rows[:, 2 + 3 * i]),
                                            ("mean", rows[:, 3 + 3 * i])], width)
            band.setData(np.repeat(tb, 2), np.column_stack((lo, hh)).ravel())
            hi.setData(tb, hh)
            if mean.isVisible():
                mean.setData(tb, mm)
            pad_hits = [(ht, pk) for ht, a, pk, _ in hits if a == adc]
            peaks.setData([h[0] for h in pad_hits], [h[1] for h in pad_hits])
            if adc in self.scaled:
                # Levels count from the resting level: the middle of the means in view.
                threshold = self.scaled[adc]
                rest = float(np.median(mm))
                for value, line in lines.items():
                    line.setPos(rest + value)
                # The scale follows the signal in view, dips below rest included:
                # it widens at once and narrows gently.
                want_lo = min(float(lo.min()), rest) - 0.1 * threshold
                want_hi = max(float(hh.max()) + 0.15 * (float(hh.max()) - rest), rest + 4 * threshold)
                cur_lo, cur_hi = self.y_top.get(adc, (want_lo, want_hi))
                cur_lo = want_lo if want_lo < cur_lo else min(want_lo, cur_lo + 0.03 * (cur_hi - cur_lo))
                cur_hi = want_hi if want_hi > cur_hi else max(want_hi, cur_hi - 0.03 * (cur_hi - cur_lo))
                self.y_top[adc] = (cur_lo, cur_hi)
                p.setYRange(max(0, cur_lo), min(4095, cur_hi), padding=0)
        self.draw_midi(start, now, notes, ccs)
        self.midi_plot.setXRange(start, now, padding=0)

    def draw_midi(self, start, now, notes, ccs):
        by_key = {}
        stems_x, stems_y = [], []
        for t_on, t_off, ch, note, vel in notes:
            by_key.setdefault((ch, note), []).append((t_on, now if t_off is None else t_off, vel))
            stems_x += [t_on, t_on]
            stems_y += [0, vel]
        for key, items in self.midi_series.items():  # series that left the window go empty
            if key[0] == "note" and (key[1], key[2]) not in by_key:
                for it in items:
                    it.setData([], [])
        for (ch, note), hits in by_key.items():
            bars, heads = self.series(("note", ch, note), f"note {note} · ch {ch}")
            bars.setData([x for t0, t1, _ in hits for x in (t0, max(t1, t0 + 0.002))],
                         [y for _, _, v in hits for y in (v, v)])
            heads.setData([h[0] for h in hits], [h[2] for h in hits])
        for (ch, cc), (ts, vs) in ccs.items():
            (line,) = self.series(("cc", ch, cc), f"CC {cc} · ch {ch}")
            line.setData(list(ts) + [now], list(vs) + [vs[-1]])
        self.stems.setData(stems_x, stems_y)

    # --- status, controls -------------------------------------------------------------

    def update_status(self):
        s, r = self.stream, self.reader
        said = s.board or r.greeting_board  # the stream's header, or the console banner
        if said in BOARDS and said != getattr(self, "_said", None):
            self._said = said
            self.board.setCurrentText(said)
        if self.flashing:
            text = "building and flashing…"
        elif r.fd is None:
            text = "looking for a midi-source board…"
        elif not s.header_done:
            text = (f"{r.port}: no stream from this firmware; Build & Flash installs the current one"
                    if r.greeting_board else f"{r.port}: starting the stream…")
        else:
            draw_ms = 1000 * sum(self.frame_times) / len(self.frame_times) if self.frame_times else 0
            pico = sum(a < 8 for a in s.inputs)
            text = f"{s.board} · {r.port} · {pico} Pico ADC inputs at {s.rate // 1000} kS/s each"
            if s.mcp3008:
                text += f", {len(s.inputs) - pico} MCP3008 channels at {s.mcp3008[0] / 1000:.1f} kS/s ({s.mcp3008[1]}-bit)"
            text += f" · drawing takes {draw_ms:.0f} ms"
        self.status.setText(text)

    def set_window(self, i):
        self.window_s = WINDOWS[i]
        self.y_top = {}
        self.drawn_version = -1

    def clear(self):
        with self.stream.lock:
            if self.stream.analog is not None:
                self.stream.analog.clear()
            self.stream.notes, self.stream.held, self.stream.ccs, self.stream.hits = [], {}, {}, []
        for items in self.midi_series.values():
            for it in items:
                it.setData([], [])
        with self.stream.lock:
            self.stream.strikes = []
        self.pad.clear()
        self.drawn_version = -1

    def build_and_flash(self):
        board = self.board.currentText()
        serial = self.reader.serial or ""  # empty: flash.sh takes the only board, or one in BOOTSEL
        self.reader.paused.set()  # the reader releases the port
        self.flash.setEnabled(False)
        self.flashing = QtCore.QProcess(self)
        self.flashing.setWorkingDirectory(str(TOOLS.parent))
        self.flashing.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.MergedChannels)
        self.flashing.readyReadStandardOutput.connect(
            lambda: [self.log.appendPlainText(l) for l in
                     bytes(self.flashing.readAllStandardOutput()).decode(errors="replace").replace("\r", "\n")
                     .splitlines() if l.strip() and "Loading into Flash" not in l])
        self.flashing.finished.connect(self.flash_done)
        command = f"tools/build.sh {board} && tools/flash.sh {board} {serial}"
        self.log.appendPlainText(f"$ {command}")
        QtCore.QTimer.singleShot(300, lambda: self.flashing.start("bash", ["-c", command]))

    def flash_done(self, code, _status):
        self.log.appendPlainText("flashed; reconnecting" if code == 0 else f"build or flash failed (exit {code})")
        self.flashing = None
        self.flash.setEnabled(True)
        self.reader.paused.clear()

    def closeEvent(self, event):
        self.reader.stop.set()
        self.reader.join(timeout=1)
        super().closeEvent(event)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="console port, when several boards are connected")
    ap.add_argument("--snapshot", metavar="PNG", help=argparse.SUPPRESS)  # for testing: save the window, quit
    ap.add_argument("--after", type=float, default=4, help=argparse.SUPPRESS)
    args = ap.parse_args()
    app = QtWidgets.QApplication(sys.argv[:1])
    pg.setConfigOptions(antialias=False)
    win = Scope(args)
    win.show()
    if args.snapshot:
        def snap():
            win.drawn_version = -1
            win.tick()
            win.grab().save(args.snapshot)
            win.close()
            app.quit()
        QtCore.QTimer.singleShot(int(args.after * 1000), snap)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
