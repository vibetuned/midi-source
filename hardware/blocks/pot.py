"""Potentiometer or fader on an ADC pin.

    +3V3 ──┐
          ┌┴┐
          │ │◄── wiper ── GP26/27/28
          └┬┘
    GNDA ──┘

The ends go to 3V3(OUT) and AGND, the wiper to an ADC pin (GP26-GP28, the
only three on the Pico). 10 kΩ-100 kΩ linear (B taper) pots all work; the
firmware oversamples and smooths. An optional capacitor from the wiper to AGND
(100 nF) steadies high-value pots on long wires.

Firmware: one entry in the pots table of src/layout.c, e.g.
    {.adc = 0, .cc = 1}    // GP26
src/inputs/pots.c maps the travel to a 7-bit CC with hysteresis. Travel ends
2% from each rail by default; a pot that doesn't reach 0 or 127 (cheap ones
can stop several percent short) gets its measured ends from the console's c:
    {.adc = 0, .cc = 1, .max = 3842}
If turning clockwise lowers the value, set .invert = true (or swap the ends).
"""

from skidl import Part

from .drawing import Block


def pot(adc, v3v3, gnda, value="10k", cap=None, title=None):
    """A pot with its wiper on the `adc` net (Pico.gpio(26..28)), ends on
    `v3v3` and `gnda`; `cap` (e.g. "100nF") adds the wiper filter capacitor."""
    rv = Part("Device", "R_Potentiometer", value=value, tag=f"pot_{adc.name}",
              footprint="Potentiometer_THT:Potentiometer_Alps_RK09K_Single_Vertical")
    rv[1] += gnda
    rv[2] += adc  # wiper
    rv[3] += v3v3
    c = None
    if cap:
        c = Part("Device", "C", value=cap, tag=f"pot_cap_{adc.name}",
                 footprint="Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm")
        c[1] += adc
        c[2] += gnda

    def draw(sheet, x, y):
        # Rotated 180: wiper to the left, toward the label; 3V3 end on top.
        pins = sheet.place(rv, x + 33.02, y + 15.24, rot=180)
        sheet.label(adc.name, (x + 12.7, y + 15.24))
        if c:
            cap_pins = sheet.place(c, x + 20.32, y + 19.05, text_at=(1.905, -0.635))
            sheet.wire((x + 12.7, y + 15.24), cap_pins["1"], pins["2"])
            sheet.power(gnda.name, cap_pins["2"])
        else:
            sheet.wire((x + 12.7, y + 15.24), pins["2"])
        sheet.wire(pins["3"], (x + 33.02, y + 7.62))
        sheet.power(v3v3.name, (x + 33.02, y + 7.62))
        sheet.wire(pins["1"], (x + 33.02, y + 22.86))
        sheet.power(gnda.name, (x + 33.02, y + 22.86))

    return Block(title or f"pot · {adc.name}", 48.26, 30.48, draw)
