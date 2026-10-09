"""Momentary push button, active low.

    GPn ──┤ SW ├── GND

Pressing the button shorts the GPIO to ground. There is no resistor: the
firmware turns on the RP2040/RP2350 internal pull-up (about 50 kΩ), so the
pin reads high at rest and low while pressed.

Firmware: one entry in the buttons table of src/layout.c, e.g.
    {.gpio = 15, .kind = BUTTON_NOTE, .number = 60}
src/inputs/buttons.c enables the pull-up and debounces (5 ms lock-out after
each edge, first edge reported at once).
"""

from skidl import Part

from .drawing import Block


def button(gpio, gnd, title=None):
    """A push button between the `gpio` net (from Pico.gpio) and `gnd`."""
    sw = Part("Switch", "SW_Push", tag=f"button_{gpio.name}", footprint="Button_Switch_THT:SW_PUSH_6mm")
    sw[1] += gpio
    sw[2] += gnd

    def draw(sheet, x, y):
        pins = sheet.place(sw, x + 25.4, y + 10.16, text_at=(-2.54, 3.81))
        sheet.label(gpio.name, (x + 12.7, y + 10.16))
        sheet.wire((x + 12.7, y + 10.16), pins["1"])
        sheet.wire(pins["2"], (x + 38.1, y + 10.16), (x + 38.1, y + 12.7))
        sheet.power(gnd.name, (x + 38.1, y + 12.7))

    return Block(title or f"button · {gpio.name}", 48.26, 22.86, draw)
