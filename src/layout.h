#ifndef LAYOUT_H
#define LAYOUT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "analog.h"
#include "mcp3008.h"

// What the instrument is made of. The tables live in layout.c; edit that file
// to describe your controls. Channel 0 in a table means channel 1.

typedef enum {
    BUTTON_NOTE,       // note on while held
    BUTTON_CC,         // CC 127 while held, 0 on release
    BUTTON_CC_TOGGLE,  // each press flips the CC between 127 and 0
    BUTTON_PROGRAM,    // program change on press
} button_kind_t;

// A push button between a GPIO and GND (the internal pull-up is used).
typedef struct {
    uint8_t gpio;
    button_kind_t kind;
    uint8_t number;    // note, controller or program number
    uint8_t channel;   // 1-16
    uint8_t velocity;  // notes only; 0 = 100
} button_def_t;

// Analog inputs (`adc` below): 0, 1, 2 are GP26, GP27, GP28, the Pico's own
// ADC; MCP3008_CH(0) to MCP3008_CH(7) are the channels of an MCP3008 on SPI,
// once the layout has one (`mcp3008`).

// A potentiometer or fader: ends on 3V3 and AGND, wiper on an ADC pin.
typedef struct {
    uint8_t adc;       // the analog input
    uint8_t cc;
    uint8_t channel;   // 1-16
    bool invert;       // wired backwards
    uint16_t min;      // raw reading (0-4095) at each end of travel, measured
    uint16_t max;      // with the console's c command; 0 = about 2% from the rail
} pot_def_t;

// A piezo drum pad on an ADC pin, behind the protection circuit of
// hardware/blocks/piezo.py. Levels count from the pad's resting level, which
// the firmware tracks.
typedef struct {
    uint8_t adc;         // the analog input
    uint8_t note;
    uint8_t channel;     // 1-16; drums usually play on 10
    uint16_t threshold;  // how far above rest a hit must rise; 0 = 150
    uint16_t max;        // the peak above rest that gives velocity 127; 0 = 4095
    bool biased;         // pulled up to ADC_VREF (the block's `bias`): rests near 1 V,
                         // so swings below rest are measured; no "unplugged" check
} pad_def_t;

// One pad read through several piezos (hardware/blocks/piezo.py, biased):
// the firmware works out where each strike landed, from the piezos' signed
// levels and the map the calibration learns (src/pad_calibration.h, written by
// the scope's Pad tab or tools/calibrate.py), and plays the note of the zone it
// fell in. The zones split the pad at (split_x, split_y), in cm on the
// calibration's axes: x to the right, y up, the top piezos at y 0.
//
// `extra` inputs are measured with every strike and streamed with it, but not
// used to place it: the scope's calibration then compares maps made with and
// without them (the same piezos read by an MCP3008 too, say). Those among the
// piezos, or that can't be used, are skipped: list every input that reads the
// pad, and whichever the calibration picks, the rest are measured.
enum { ZONE_TOP_LEFT, ZONE_TOP_RIGHT, ZONE_BOTTOM_LEFT, ZONE_BOTTOM_RIGHT, ZONE_COUNT };
#define ZONE_MAX_INPUTS 8  // piezos and (kept) extra inputs together

typedef struct {
    uint8_t piezos;                     // how many locate strikes: PAD_CALIBRATION_PIEZOS
    uint8_t adc[ZONE_MAX_INPUTS];       // those inputs, in the calibration's order: PAD_CALIBRATION_ADC
    float map[ZONE_MAX_INPUTS + 1][2];  // signature (and 1) -> (x, y) cm: PAD_CALIBRATION_MAP
    uint8_t extra[ZONE_MAX_INPUTS];     // inputs only measured, for calibrating
    uint8_t extra_count;
    float split_x, split_y;
    uint8_t notes[ZONE_COUNT];
    uint8_t channel;       // 1-16; drums usually play on 10
    uint16_t threshold;    // how far above rest a piezo must rise to start a strike; 0 = 150
    uint16_t max;          // strike strength (length of the piezos' levels) for velocity 127; 0 = 2400
    const char *calibration;  // the calibration run the map came from (PAD_CALIBRATION_RUN), for the scope
} zone_pad_def_t;

typedef struct {
    const button_def_t *buttons;
    size_t button_count;
    const pot_def_t *pots;
    size_t pot_count;
    const pad_def_t *pads;
    size_t pad_count;
    const zone_pad_def_t *zone_pads;  // at most one
    size_t zone_pad_count;
    const mcp3008_def_t *mcp3008;     // an MCP3008 (or MCP3208) on SPI, or NULL
} layout_t;

extern const layout_t layout;

static inline uint8_t layout_channel(uint8_t channel) {
    return channel ? channel : 1;
}

// Whether a control can be used: its pin exists on every board and no earlier
// control claims it (the MCP3008's pins, then buttons, pots, pads, zone pads).
// The scanners skip the rest.
bool layout_mcp3008_ok(void);
bool layout_button_ok(size_t index);
bool layout_pot_ok(size_t index);
bool layout_pad_ok(size_t index);
bool layout_zone_pad_ok(size_t index);

// A zone pad's inputs: its piezos, then its extra inputs that aren't among
// them and can be used. Returns how many.
size_t layout_zone_inputs(const zone_pad_def_t *zone_pad, uint8_t out[ZONE_MAX_INPUTS]);

// Print every control that can't be used and why; returns how many.
int layout_check(bool print);

#endif
