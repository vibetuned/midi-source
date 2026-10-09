"""Piezo drum pad on an ADC pin, with the protection a Pico needs.

    piezo + ──┬──────┬──[10k]──┬───────┬────────┬──── GP26/27/28
              │      │         │       │        │
            piezo  [1M]    zener ▲   1N5817 ▲  [100k]   (both bands toward the pin)
              │      │      3V3│       │        │
    piezo − ──┴──────┴─────────┴───────┴────────┴──── GNDA

A hard hit makes a piezo swing tens of volts both ways; the ADC pins must
stay within about 0-3.3 V.
- 1 MΩ drains the piezo, so the pin rests at 0 between hits.
- 10 kΩ limits the current the diodes have to take.
- 100 kΩ from the pin to GNDA loads the piezo: it drains in milliseconds
  instead of the 1 MΩ's tens (whose slow tail retriggered the pad), and the
  hits stay further below the diodes' knees. 10 kΩ here was too much: medium
  hits fell from about 2000 to 320.
- The 3V3 zener clamps the positive swing (a 3V0 also works; its softer knee
  compresses the hardest hits a little more). Never use one above 3V6.
- The Schottky clamps the negative swing at about -0.3 V; the zener alone would
  let the pin reach -0.7 V, past the chip's -0.5 V limit.

Mounting: tape or glue the disc under the pad (on an Evans practice pad, under
the wooden base near the center), brass side down, with foam under the pad to
keep table knocks out.

Firmware: one entry in the pads table of src/layout.c, e.g.
    {.adc = 1, .note = 38, .channel = 10}    // GP27, snare
src/inputs/pads.c finds each hit's peak in the 500 kS/s sample stream and
sends a note on with a velocity from it. Tune .threshold and .max from the
peaks the console's p reports. An unwired pin is detected and kept silent.

Measured on an Evans practice pad with these values (2026-10-08): hits peak
at about 240 (soft), 940 (medium) and 2300 (hard), are back to the resting
noise (peaks of about 65) within 20 ms, and rebound to about 100 near 30 ms.
Without the 100 kΩ the range was compressed to 1500-3000 and a tail of about
200 lingered at 130 ms.
"""

from skidl import Net, Part

from .drawing import Block


def piezo_pad(adc, gnda, zener="3V3", series="10k", bleed="1M", load="100k", bias=None, vref=None, title=None):
    """A piezo pad on the `adc` net (Pico.gpio(26..28)) with its protection,
    grounded on `gnda`. The piezo's footprint is a 2-pin header for its leads.

    `bias` (e.g. "220k", with `vref` = Pico.vref) adds a resistor from the pin
    to ADC_VREF: with the 100 kΩ load the pin rests near 1 V instead of 0, so
    the piezo's swings below rest (the board bending away from it) are measured
    too. Locating hits on a multi-piezo pad needs them. Mark the pad
    `.biased = true` in src/layout.c."""
    pz = Part("Device", "Microphone_Crystal", value="Piezo", tag=f"piezo_{adc.name}",
              footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
    r_bleed = Part("Device", "R", value=bleed, tag=f"piezo_bleed_{adc.name}",
                   footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    r_series = Part("Device", "R", value=series, tag=f"piezo_series_{adc.name}",
                    footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    dz = Part("Device", "D_Zener", value=zener, tag=f"piezo_zener_{adc.name}",
              footprint="Diode_THT:D_DO-35_SOD27_P7.62mm_Horizontal")
    ds = Part("Device", "D_Schottky", value="1N5817", tag=f"piezo_schottky_{adc.name}",
              footprint="Diode_THT:D_DO-41_SOD81_P10.16mm_Horizontal")
    r_load = Part("Device", "R", value=load, tag=f"piezo_load_{adc.name}",
                  footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")

    # Pin numbers: the piezo's 2 is +, 1 is -; a diode's 1 is K, 2 is A.
    raw = Net(f"PIEZO_{adc.name}")  # the piezo side of the series resistor
    raw += pz[2], r_bleed[1], r_series[1]
    r_series[2] += adc
    pz[1] += gnda
    r_bleed[2] += gnda
    dz[1, 2] += adc, gnda  # reverse biased: conducts above 3.3 V
    ds[1, 2] += adc, gnda  # forward biased below about -0.3 V
    r_load[1, 2] += adc, gnda
    r_bias = None
    if bias:
        r_bias = Part("Device", "R", value=bias, tag=f"piezo_bias_{adc.name}",
                      footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
        r_bias[1, 2] += vref, adc

    def draw(sheet, x, y):
        dy = 7.62 if r_bias else 0.0  # room above the signal line for the bias resistor
        rail, ground, mid = y + 10.16 + dy, y + 20.32 + dy, y + 13.97 + dy
        sheet.label(adc.name, (x + 12.7, rail))
        col, taps = x + 20.32, []
        if r_bias:
            b = sheet.place(r_bias, col, rail - 3.81)  # pin 1 up, to ADC_VREF; pin 2 on the signal line
            sheet.label(vref.name, b["1"], side="right")
            taps.append(b["2"])
            col += 10.16
        # Diodes rotated 270 put the cathode (K) on top, toward the signal.
        z = sheet.place(dz, col, mid, rot=270)
        d = sheet.place(ds, col + 10.16, mid, rot=270)
        lo = sheet.place(r_load, col + 22.86, mid)
        se = sheet.place(r_series, col + 36.83, rail, rot=270, text_at=(-2.54, -5.08))  # pin 2 left
        bl = sheet.place(r_bleed, col + 48.26, mid)
        pzp = sheet.place(pz, col + 60.96, y + 15.24 + dy)
        sheet.wire((x + 12.7, rail), *taps, z["1"], d["1"], lo["1"], se["2"])
        sheet.wire(se["1"], bl["1"], pzp["2"])
        for bottom in (z["2"], d["2"], lo["2"], bl["2"]):
            sheet.wire(bottom, (bottom[0], ground))
            sheet.power(gnda.name, (bottom[0], ground))
        sheet.power(gnda.name, pzp["1"])

    extra = 7.62 if r_bias else 0.0
    return Block(title or f"piezo pad · {adc.name}", 96.52 + (10.16 if r_bias else 0), 30.48 + extra, draw)
