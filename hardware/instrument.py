"""The instrument as wired today; keep it in step with src/layout.c.

    uv run python instrument.py

writes build/instrument/: the KiCad schematic (one sheet: the Pico on the
left, each block in a frame), a netlist for the PCB editor, and a PDF.
"""

from blocks import write_kicad
from blocks.button import button
from blocks.mcp3008 import mcp3008
from blocks.pico import Pico
from blocks.piezo import piezo_pad
from skidl import POWER, Net

gnd = Net("GND")
gnd.drive = POWER

pico = Pico(gnd)
# The three piezos under the Evans pad (the zone pad), biased so swings below
# rest are measured.
piezo = dict(gnda=pico.gnda, bias="220k", vref=pico.vref)
blocks = [
    button(pico.gpio(15), gnd, title="button · GP15 · C4"),
    piezo_pad(pico.gpio(26), **piezo, title="piezo top-left · GP26 · zone pad"),
    piezo_pad(pico.gpio(27), **piezo, title="piezo bottom · GP27 · zone pad"),
    piezo_pad(pico.gpio(28), **piezo, title="piezo top-right · GP28 · zone pad"),
    # The same three piezos read a second time, by the MCP3008: CH0-CH2 on the
    # GP26-GP28 nodes, to compare the two ADCs on the same strikes.
    mcp3008(pico.v3v3, gnd, pico.gnda, sck=pico.gpio(18), mosi=pico.gpio(19), miso=pico.gpio(16),
            cs=pico.gpio(17), inputs={0: pico.gpio(26), 1: pico.gpio(27), 2: pico.gpio(28)},
            title="MCP3008 · SPI0 pins · CH0-CH2 = GP26-GP28"),
]
pico.finish()

write_kicad("instrument", "midi-source", pico, blocks)
