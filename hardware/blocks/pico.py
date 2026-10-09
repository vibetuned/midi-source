"""The microcontroller module: a Raspberry Pi Pico, Pico W, Pico 2 or Pico 2 W.

All four share the same 40-pin footprint and pinout, so one symbol serves the
whole family. The firmware build picks the board (tools/build.sh <board>).
"""

from skidl import POWER, Net, Part

# The pins every board brings out for general use (src/layout.c checks the same).
USABLE_GPIO = [*range(0, 23), 26, 27, 28]


class Pico:
    """The module. Blocks connect to its pins through named nets (GP15...),
    which become the labels joining the block sheets to this one."""

    def __init__(self, gnd, ref="U1"):
        self.part = Part("MCU_Module", "RaspberryPi_Pico", ref=ref, tag=ref)
        for pin in self.part.pins:
            if pin.name == "GND":  # AGND stays separate, for the ADC blocks
                pin += gnd
        self._nets = {}
        self._v3v3 = self._gnda = self._vref = None

    @property
    def v3v3(self):
        """3V3(OUT), pin 36: the module's regulator, up to 300 mA for blocks."""
        if self._v3v3 is None:
            self._v3v3 = Net("+3V3")
            self._v3v3 += self.part["3V3"]
        return self._v3v3

    @property
    def gnda(self):
        """AGND, pin 33: the quiet ground for whatever feeds the ADC pins.
        Named GNDA, KiCad's analog-ground power symbol."""
        if self._gnda is None:
            self._gnda = Net("GNDA")
            self._gnda.stub = True  # drawn as power symbols, not a wire across sheets
            self._gnda += self.part["AGND"]
        return self._gnda

    @property
    def vref(self):
        """ADC_VREF, pin 35: the ADC's reference, the module's filtered 3.3 V.
        Biasing an ADC input from it keeps supply ripple out of the reading."""
        if self._vref is None:
            self._vref = Net("ADC_VREF")
            self._vref.drive = POWER  # the module makes it; KiCad sees an input pin
            self._vref += self.part["ADC_VREF"]
        return self._vref

    def gpio(self, n):
        """The net on GPn (GP26-GP28 are also the ADC inputs)."""
        if n not in USABLE_GPIO:
            raise ValueError(f"GP{n} is not usable on every Pico board")
        if n not in self._nets:
            net = Net(f"GP{n}")
            net += self.part[f"GPIO{n}_ADC{n - 26}" if n >= 26 else f"GPIO{n}"]
            self._nets[n] = net
        return self._nets[n]

    def finish(self):
        """Mark every pin no block used as not connected, so ERC stays clean.
        Call once, after the last block."""
        for pin in self.part.pins:
            if not pin.is_connected():
                pin += self.part.circuit.NC
