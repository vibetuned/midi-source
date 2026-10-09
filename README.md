# midi-source

Firmware that turns a Raspberry Pi Pico, Pico W, Pico 2 or Pico 2 W into a
class-compliant USB MIDI controller. macOS, Windows, Linux, iPadOS and Android
see it without drivers.

| | |
|---|---|
| Working | USB MIDI and a serial console; buttons sending notes, CCs or program changes; pots and faders sending CCs; piezo drum pads with velocity |
| Next | hit position from three piezos (triangulation), velocity-sensitive keys, FSR pads with aftertouch, rotary encoders, an analog multiplexer for more inputs, MPE |

## Toolchain

Everything lives in `~/.pico-sdk`, the folder the Raspberry Pi Pico VS Code
extension uses, so the extension and the command line share one copy:

| | Version | Path |
|---|---|---|
| Pico SDK (with TinyUSB 0.18) | 2.3.1 | `~/.pico-sdk/sdk/2.3.1` |
| Arm GNU Toolchain | 15.2.rel1 | `~/.pico-sdk/toolchain/15_2_Rel1` |
| picotool | 2.3.1 | `~/.pico-sdk/picotool/2.3.1/picotool` |
| pioasm | 2.3.1 | `~/.pico-sdk/tools/2.3.1/pioasm` |

CMake and Ninja come from Homebrew. To use the extension, open its sidebar and
choose **Import Project** with SDK 2.3.1.

## Build

```sh
tools/build.sh            # all four boards
tools/build.sh pico2      # one board: pico, pico_w, pico2 or pico2_w
```

Each board builds in `build/<board>/`; the file to flash is `midi_source.uf2`.

## Flash

The first time, hold **BOOTSEL** while plugging the board in, then:

```sh
tools/flash.sh pico2
```

or drag `build/pico2/midi_source.uf2` onto the drive that appears. After that,
`tools/flash.sh` needs no button: it opens the console port at 1200 baud,
which reboots the running firmware into BOOTSEL, then loads the new build.

With several boards plugged in, list them and pass the serial of the one to
flash:

```sh
tools/boards.py                          # every Pico: running midi-source, BOOTSEL or other
tools/flash.sh pico E663B035971F5E25
```

## Wiring

The layout in [src/layout.c](src/layout.c):

| Control | Wiring | Sends |
|---|---|---|
| 1 button | GP15 to GND | C4 (middle C), channel 1 |
| 3 piezos under one Evans practice pad | each through the protection circuit below, to GP26, GP27 and GP28 | a zone pad: each strike is located and plays its zone's tom, top-left 50, top-right 48, bottom-left 47, bottom-right 45, channel 10 |
| MCP3008 | below; CH0, CH1, CH2 to the GP26, GP27, GP28 nodes | the same three piezos read a second time, to compare the ADCs |

A pot goes on an ADC pin the piezos don't use: its wiper to GP26-GP28, its
ends to 3V3(OUT) (pin 36) and AGND (pin 33).

The piezo needs protection: a hard hit makes it swing tens of volts.

```
                           ADC_VREF (pin 35)
                               │
                            [220k]      the bias, for pads that locate hits
                               │
piezo red ──┬────────┬──[10k]──┼───────┬───────┬────────┬───── GP26 / 27 / 28
            │        │                 │       │        │
          piezo    [1M]             zener   1N5817   [100k]   both diode bands (cathodes) face the pin
            │        │               3V3     │        │
piezo black ┴────────┴─────────────────┴─────┴────────┴──── AGND
```

The 100 kΩ drains the piezo within milliseconds; without it, a hit leaves a
slow tail that retriggers the pad. The 220 kΩ to ADC_VREF (one per piezo)
rests the pin near 1 V instead of 0, so the piezo's swings below rest are
measured too: when the board bends away from a piezo it reads below rest,
and locating hits on a multi-piezo pad needs that. Mark such pads
`.biased = true`; a single pad that only plays doesn't need the bias.

Use a 3V3 zener (3V0 also works; never above 3V6). Until the circuit is
wired, the pad stays silent: the console's `p` reports the floating pin.

The MCP3008 (16-pin DIP, notch up: pin 1 top left, pin 16 top right):

| MCP3008 pin | to |
|---|---|
| 16 Vdd, 15 Vref | 3V3(OUT), pin 36; 100 nF close to the chip from Vdd to GND and from Vref to AGND |
| 14 AGND | AGND, pin 33 |
| 13 CLK | GP18 (pin 24) |
| 12 Dout | GP16 (pin 21) |
| 11 Din | GP19 (pin 25) |
| 10 CS | GP17 (pin 22) |
| 9 DGND | GND, e.g. pin 23 |
| 1 CH0, 2 CH1, 3 CH2 | the GP26, GP27, GP28 nodes, each with 1 nF to AGND |

Vref goes to 3V3, not ADC_VREF: the chip draws on its reference at every
conversion, and ADC_VREF feeds the Pico's own ADC. The 1 nF holds each input
steady while the chip samples it. Without the chip the firmware still runs:
its channels read full scale (Dout is pulled up), the calibration leaves them
out, and the console's `s` shows how often each of the four pins changed,
saying so when the chip never answers.

Linear pots from 10 kΩ to 100 kΩ work. Only GP26, GP27 and GP28 can read
pots (or an MCP3008's channels). List only the pots that are wired: an unconnected ADC pin floats and
would send random CCs. Usable pins on every board are GP0 to GP22 and GP26 to
GP28.

The LED blinks slowly until a computer mounts the device, then stays lit; each
MIDI message makes it flicker.

## Console

```sh
screen /dev/cu.usbmodem* 115200      # quit with Ctrl-A then K
tools/console.py s p                 # or one-shot: send commands, print replies
tools/console.py --record 3 --timeout 60   # record the next 3 pad hits
```

| Key | |
|---|---|
| `m` | MIDI monitor: print every message in and out |
| `s` | stats: scan timing, messages sent and dropped |
| `p` | pots (raw reading, travel, position, CC value) and pads (hits, last peak and velocity, loudest) |
| `c` | calibrate pots: press, turn each pot end to end, press again; prints layout lines with `.min` / `.max` |
| `w` | record the next pad hit: its highest sample of every millisecond for 200 ms |
| `V` / `v` | start / stop the live stream that `tools/scope.py` reads |
| `l` | check the layout for unusable or doubly used pins |
| `b` | reboot into BOOTSEL |
| `h` | help |

## Scope

```sh
uv run tools/scope.py
```

A window showing what the board sees and what it sends, live, on one time
axis:

- **One plot per analog input,** for every millisecond: the band from its
  lowest to its highest sample, and the mean. The ADC takes 500,000 samples a
  second, and the band keeps every piezo peak. Pads also show their threshold,
  a white dot on each hit's peak, and a vertical scale that follows the hits
  in view.
- **The MIDI out:** each note is a stem at its velocity, with a bar for as
  long as it lasts, and each CC is a stepped line.

The board timestamps both, so a hit and the note it caused line up exactly.
**Pause** freezes the view so you can drag and scroll into it. **Build &
Flash** rebuilds the firmware for the board picked next to it, flashes it and
reconnects. The board sets the picker itself (from the stream, or from the
console greeting of an older build without one). uv installs the window's libraries (Qt, pyqtgraph) on the first
run.

The **Pad** tab shows the pad from above: its piezos, the calibration spots,
the zone pad's zones, and where each strike landed (the newest large and
yellow, older ones fading, size by velocity). It uses the newest calibration in
`build/calibration/`, and says so when the board still runs an older one.

### Locating hits

A pad with three piezos under it (the Evans pad: two 7 cm apart near the top,
the third 10 cm from each, every piezo biased) can tell where it was hit. To
calibrate it, press **Calibrate…** on the Pad tab (the board needs its zone
pad, see below):

1. A ring shows the spot to hit: first right above each piezo (that tells
   which pin is which), then between them and in the centre, 7 spots in all.
   Hit each one 4 times, medium strength, about a second apart. The ring moves
   on by itself. **Backspace** undoes a strike, **Escape** cancels. A strike
   within 0.12 s of the last one is taken for a bounce and skipped.
2. At the end it shows where the new map puts every strike (dots, joined to
   their spot) next to where the board put it with its current calibration
   (rings), with both errors, and the error to expect between spots (each spot
   located by a map made without it).
3. **Save** writes `build/calibration/<time>/` (the strikes and
   `calibration.json`) and `src/pad_calibration.h`. **Save, Build & Flash**
   also puts it in the board.

With the MCP3008 reading the same piezos, step 2 makes a map from each ADC and
one from both, all from the same strikes, and shows their errors side by side.
Pick the one to save from the list. An ADC that heard nothing (the chip not
connected) is left out, with a note saying so.

It calibrates from the levels the board itself measures for each strike (the
stream's `L` lines), so the map learns exactly what the firmware will see.
Each strike's signature is its three signed levels (up for the piezos near the
hit, down where the board bends away) divided by their length, so strength
drops out. The pad bends like a plate, not like ripples from a point, so the
calibration learns a linear map from signature to place instead of a formula
of distance ([tools/padmap.py](tools/padmap.py)). The first run gave about
0.9 cm at the spots and 1.5 cm between them (median).

`uv run tools/calibrate.py` does the same in the terminal from raw captures
(close the scope first). It also works without a zone pad, and
`--refit build/calibration/<run>` re-analyses a saved run of either kind.
`tools/hits.py` captures single hits' raw samples for closer study.

The stream is console command `V`, described in [src/stream.h](src/stream.h).
If the link falls behind, the board drops frames rather than delaying the
instrument. The window reads the stream on its own thread, so slow drawing
lowers its frame rate but never makes it fall behind the board.

## Your own layout

Edit the tables in [src/layout.c](src/layout.c). A button is one line:

```c
{.gpio = 10, .kind = BUTTON_NOTE, .number = 74},            // D5
{.gpio = 11, .kind = BUTTON_CC_TOGGLE, .number = 64},       // sustain latch
{.gpio = 12, .kind = BUTTON_PROGRAM, .number = 5, .channel = 2},
```

A pot is one line too: `{.adc = 1, .cc = 74}` reads GP27 (`.adc` 0, 1, 2 is
GP26, GP27, GP28); add `.invert = true` if it turns the wrong way. If a pot
doesn't reach 0 or 127, its ends fall short of the rails: measure them with the
console's `c` and paste the line it prints, e.g.
`{.adc = 0, .cc = 1, .max = 3842}`.

A pad: `{.adc = 1, .note = 38, .channel = 10}`. Its levels count from its
resting level, which the firmware tracks. Hit it softly and hard and read the
peaks with `p` (or record a hit's envelope with `w`), then set
`.threshold` (the softest hit that should play, default 150) and `.max` (the peak that gives velocity 127, default
4095).

A zone pad is one pad read through three or more piezos: each strike is located (with
the calibration the scope's Pad tab writes to `src/pad_calibration.h`) and
plays the note of the zone it landed in. The zones split the pad at
`.split_x`, `.split_y` (cm; by default the centre of the piezos' triangle),
and `.notes` lists top-left, top-right, bottom-left, bottom-right. After a
strike the pad waits 15 ms, then a new one must rise above a threshold that
starts at 40% of the last strike and falls back over 60 ms, so a second stick
can play right after the first.

An MCP3008 adds eight analog inputs: `.mcp3008 = &mcp3008` with its pins
(`{.miso = 16, .cs = 17, .sck = 18, .mosi = 19}`, the Pico's SPI0 pins; add
`.bits = 12` for an MCP3208), and then `MCP3008_CH(0)` to `MCP3008_CH(7)` work
anywhere an `.adc` goes. A zone pad's `.extra` inputs are measured with every
strike but don't place it; those its map already uses are skipped. The layout
lists all six inputs that read the pad (GP26-GP28 and, on the same piezos,
CH0-CH2): whichever three the calibration picks place the strikes, and the
other three are still measured, so the next calibration compares the ADCs
again.

Channels are 1 to 16, and 0 means 1. A control whose pin can't be used is
skipped, and the console prints why when it connects.

## Hardware

The schematics are Python, in [hardware/](hardware/), written with
[SKiDL](https://github.com/devbisme/skidl) against the KiCad 10 libraries.
Each block is reusable and pairs with a firmware module:

| Block | Circuit | Firmware |
|---|---|---|
| [pico.py](hardware/blocks/pico.py) | Pico, Pico W, Pico 2 or Pico 2 W (same pinout) | the board given to `tools/build.sh` |
| [button.py](hardware/blocks/button.py) | push button from a GPIO to GND, internal pull-up, active low | a `BUTTON_*` entry in the buttons table |
| [pot.py](hardware/blocks/pot.py) | pot between 3V3 and AGND, wiper on GP26-GP28, optional filter capacitor | an entry in the pots table |
| [piezo.py](hardware/blocks/piezo.py) | piezo with 1 MΩ bleed, 10 kΩ series, 3V3 zener and 1N5817 clamps, on GP26-GP28 | an entry in the pads table |
| [mcp3008.py](hardware/blocks/mcp3008.py) | MCP3008 (or MCP3208) on four GPIOs (CS and CLK consecutive), 1 nF on each input | `.mcp3008` in the layout; its channels are inputs `MCP3008_CH(0)`-`MCP3008_CH(7)` |

A design puts blocks together. [instrument.py](hardware/instrument.py) is the
hardware the current layout describes, so keep the two in step:

```sh
cd hardware
uv run python instrument.py    # writes build/instrument/: .kicad_sch, .net, .pdf
```

Open `hardware/build/instrument/instrument.kicad_sch` in KiCad (or the PDF
next to it): one sheet with the Pico on the left, every pin labelled, powered
or marked not-connected, and each block in a titled frame on the right, joined
by net labels (`GP15`, `GP26`, `GP27`) and power symbols (`GND`, `GNDA`,
`+3V3`).

SKiDL decides what connects to what; each block's `draw` places its parts by
hand ([drawing.py](hardware/blocks/drawing.py) writes the sheet). Every run
checks the drawing against the circuit: KiCad's netlist of the sheet must
connect exactly the pins SKiDL connects, and KiCad's ERC must find no errors.
The files are regenerated each time, so make changes in the Python.

## How it works

- **Core 0** runs TinyUSB (MIDI and the console), the LED, and moves MIDI from
  the instrument to USB ([main.c](src/main.c), [midi_out.c](src/midi_out.c)).
- **Core 1** scans every control at a fixed 1 kHz
  ([instrument.c](src/instrument.c)) and queues MIDI messages to core 0, so
  sensor timing never waits on USB.
- **Buttons** report the first edge immediately, then ignore the pin for 5 ms
  while the contacts bounce. Debouncing adds no latency.
- **The ADC never stops** ([analog.c](src/analog.c)): it cycles through the
  analog pins in use at 500 kS/s in total, and DMA writes every sample into a
  ring buffer. Each scan splits the new samples by pin.
- **An MCP3008 never stops either** ([mcp3008.c](src/mcp3008.c)): a PIO state
  machine ([mcp3008.pio](src/mcp3008.pio)) clocks one conversion after
  another, about 70,000 a second at 3.3 V shared by its channels. One DMA
  channel feeds it each channel's command, round and round; another writes the
  results into a ring like the Pico ADC's. Its 10-bit samples are scaled to
  12 bits, and each sample's time is known, so a zone pad can mix inputs of
  both ADCs.
- **Pots** average their samples, are smoothed, and map their travel (2% from
  each rail, or calibrated ends) to a CC. Hysteresis keeps a pot resting
  between two CC values from flickering.
- **Pads** watch every sample: a hit crosses the threshold, its peak over the
  next 2 ms sets the velocity (on a square-root curve), the pad ignores its ringing and stick bounce
  for 50 ms, and
  the threshold then falls back from half the peak over 100 ms, so ringing
  can't retrigger but a fast second hit still does.

## USB IDs

The firmware uses TinyUSB's development IDs (`0xCafe:0x4009`). Before giving
boards to other people, request a free PID under the Raspberry Pi vendor ID:
<https://github.com/raspberrypi/usb-pid>.
