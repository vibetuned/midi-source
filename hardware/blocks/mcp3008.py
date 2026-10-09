"""MCP3008: eight more analog inputs on SPI, read by a PIO state machine.

                 +3V3
                  │
            ┌─ Vdd, Vref ─┐            100 nF from Vdd to GND and from Vref to
    CH0 ────┤             ├── CLK ──── GP18      GNDA, close to the chip
    CH1 ────┤   MCP3008   ├── Dout ─── GP16
     …      │             ├── Din ──── GP19
    CH7 ────┤             ├── CS ───── GP17
            └─ AGND  DGND ┘
                GNDA  GND

A 10-bit ADC (the MCP3208 is the 12-bit one, pin for pin the same: set .bits
= 12). At 3.3 V it converts about 70,000 times a second in all, shared by the
channels in use: with three, each gets about 17,500 samples a second (four
slots per round; the third channel's slot repeats). Plenty for a piezo's
2 ms bump.

- Vref on 3V3 makes its full scale 3.3 V, like the Pico's ADC. Not on the
  Pico's ADC_VREF: the chip draws on it at every conversion, and that pin
  feeds the Pico's own ADC through 200 Ω.
- 1 nF from each channel in use to GNDA: the chip samples by charging 20 pF
  for about a microsecond, and a piezo's circuit can't deliver that through
  its 10 kΩ in time. The capacitor holds the voltage while it does.
- CS and CLK must be consecutive GPIOs (the state machine drives both as one
  side-set pair): GP17 and GP18 here, the Pico's SPI0 pins.

Firmware: in src/layout.c, `.mcp3008 = &mcp3008` with the pins, then its
channels are inputs MCP3008_CH(0)-MCP3008_CH(7) for pots, pads and the zone
pad. src/mcp3008.c and src/mcp3008.pio run the conversions; DMA writes every
sample into a ring buffer like the Pico's own ADC.
"""

from skidl import Part

from .drawing import Block

DIP = "Package_DIP:DIP-16_W7.62mm"
CAP = "Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm"


def mcp3008(v3v3, gnd, gnda, sck, mosi, miso, cs, inputs, input_cap="1nF", part="MCP3008", title=None):
    """The ADC on SPI nets `sck`, `mosi` (its Din), `miso` (its Dout) and `cs`
    (Pico.gpio(...); cs and sck consecutive), powered from `v3v3`. `inputs`
    maps channel numbers to the nets they read, e.g. {0: pico.gpio(26)}; each
    gets an `input_cap` to GNDA. Unused channels are left open."""
    u = Part("Analog_ADC", part, tag="mcp3008", footprint=DIP)
    u["Vdd"] += v3v3
    u["Vref"] += v3v3
    u["DGND"] += gnd
    u["AGND"] += gnda
    u["CLK"] += sck
    u["Din"] += mosi
    u["Dout"] += miso
    u["~{CS}/SHDN"] += cs
    c_vdd = Part("Device", "C", value="100nF", tag="mcp3008_vdd", footprint=CAP)
    c_ref = Part("Device", "C", value="100nF", tag="mcp3008_vref", footprint=CAP)
    c_vdd[1, 2] += v3v3, gnd
    c_ref[1, 2] += v3v3, gnda
    caps = {}
    for ch, net in sorted(inputs.items()):
        u[f"CH{ch}"] += net
        if input_cap:
            caps[ch] = Part("Device", "C", value=input_cap, tag=f"mcp3008_in{ch}", footprint=CAP)
            caps[ch][1, 2] += net, gnda
    for ch in range(8):
        if ch not in inputs:
            u[f"CH{ch}"] += u.circuit.NC

    def draw(sheet, x, y):
        cx, cy = x + 30.48, y + 25.4
        p = sheet.place(u, cx, cy, text_at=(-15.24, -16.51))
        for ch in range(8):
            at = p[str(ch + 1)]
            if ch in inputs:
                end = (at[0] - 5.08, at[1])
                sheet.wire(at, end)
                sheet.label(inputs[ch].name, end)
            else:
                sheet.no_connect(at)
        for pin, net in (("13", sck), ("12", miso), ("11", mosi), ("10", cs)):
            end = (p[pin][0] + 5.08, p[pin][1])
            sheet.wire(p[pin], end)
            sheet.label(net.name, end, side="right")
        for pin in ("15", "16"):  # Vref, Vdd: up to +3V3
            top = (p[pin][0], p[pin][1] - 2.54)
            sheet.wire(p[pin], top)
            sheet.power(v3v3.name, top)
        sheet.wire(p["14"], (p["14"][0], p["14"][1] + 2.54))
        sheet.power(gnda.name, (p["14"][0], p["14"][1] + 2.54))
        sheet.wire(p["9"], (p["9"][0], p["9"][1] + 2.54))
        sheet.power(gnd.name, (p["9"][0], p["9"][1] + 2.54))
        # The supply capacitors, to the right; the input capacitors in a row below.
        for i, (c, ground) in enumerate(((c_vdd, gnd), (c_ref, gnda))):
            cp = sheet.place(c, x + 66.04 + 10.16 * i, y + 15.24, text_at=(1.905, -0.635))
            sheet.wire(cp["1"], (cp["1"][0], cp["1"][1] - 2.54))
            sheet.power(v3v3.name, (cp["1"][0], cp["1"][1] - 2.54))
            sheet.power(ground.name, cp["2"])
        for i, ch in enumerate(sorted(caps)):
            cp = sheet.place(caps[ch], x + 15.24 + 12.7 * i, y + 59.69, text_at=(1.905, -0.635))
            top = (cp["1"][0], cp["1"][1] - 2.54)
            sheet.wire(cp["1"], top)
            sheet.label(inputs[ch].name, top, side="right")
            sheet.power(gnda.name, cp["2"])

    return Block(title or f"{part} · SPI", 86.36, 68.58 if caps else 48.26, draw)
