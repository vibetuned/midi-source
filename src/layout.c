// The instrument: which pin does what. Edit the tables below.

#include "layout.h"

#include <stdio.h>

#include "pad_calibration.h"

#include "pico/stdlib.h"

// Buttons, each wired between its GPIO and GND.
static const button_def_t buttons[] = {
    {.gpio = 15, .kind = BUTTON_NOTE, .number = 60},  // C4 (middle C)
    // More notes, e.g. the rest of a C major scale:
    // {.gpio = 2, .kind = BUTTON_NOTE, .number = 62},  // D4
    // {.gpio = 3, .kind = BUTTON_NOTE, .number = 64},  // E4
    // Other kinds:
    // {.gpio = 10, .kind = BUTTON_CC_TOGGLE, .number = 64},  // sustain latch
    // {.gpio = 11, .kind = BUTTON_PROGRAM, .number = 0},
};

// Pots: ends to 3V3 and AGND, wiper to the ADC pin. List only pots that are
// wired; an unconnected ADC pin floats and would spray random CCs. If a pot
// doesn't reach 0 or 127, measure its .min / .max with the console's c.
// None on this board: its three ADC pins read the pad's piezos. Uncomment the
// table and the two .pots lines in `layout` below to add some, e.g.
// static const pot_def_t pots[] = {
//     {.adc = 0, .cc = 1, .max = 3842},  // GP26: modulation; a 100k that tops out at 94%
// };

// Piezo pads, each behind the protection circuit (hardware/blocks/piezo.py),
// one note per pad. Tune .threshold and .max from the peaks the console's p
// reports, or watch them in tools/scope.py. None on this board: its piezos
// make the zone pad below. A single pad would be e.g.
// static const pad_def_t pads[] = {
//     {.adc = 1, .note = 38, .channel = 10, .max = 2400},  // GP27: snare
// };

// An MCP3008 on the Pico's SPI0 pins (hardware/blocks/mcp3008.py): Dout GP16,
// CS GP17, CLK GP18, Din GP19. For an MCP3208 add .bits = 12.
static const mcp3008_def_t mcp3008 = {.miso = 16, .cs = 17, .sck = 18, .mosi = 19};

// The zone pad: three piezos under one Evans practice pad (GP26 top-left,
// GP28 top-right 7 cm from it, GP27 below them 10 cm from each; each biased,
// 220k to ADC_VREF). Each strike is located with the calibration in
// pad_calibration.h (the scope's Pad tab writes it) and plays its zone's note.
// The zones split at the triangle's centre; watch the Pad tab of
// tools/scope.py to move the split.
//
// The MCP3008's CH0-CH2 are wired to the same three piezos. Every input that
// reads the pad is listed as an extra: whichever ones the calibration's map
// uses locate the strikes, and the others are measured with each strike, so
// the next calibration can compare the two ADCs again. Without the chip its
// channels read full scale (Dout is pulled up) and the calibration leaves them
// out; the console's s says the chip isn't answering.
static const zone_pad_def_t zone_pads[] = {
    {
        .piezos = PAD_CALIBRATION_PIEZOS,
        .adc = PAD_CALIBRATION_ADC,
        .map = PAD_CALIBRATION_MAP,
        .extra = {0, 1, 2, MCP3008_CH(0), MCP3008_CH(1), MCP3008_CH(2)},
        .extra_count = 6,
        .split_x = PAD_CENTRE_X,
        .split_y = PAD_CENTRE_Y,
        .calibration = PAD_CALIBRATION_RUN,
        .notes = {50, 48, 47, 45},  // toms: high, high-mid, low-mid, low
        .channel = 10,
        .max = 2400,
    },
};

const layout_t layout = {
    .buttons = buttons,
    .button_count = count_of(buttons),
    // .pots = pots,
    // .pot_count = count_of(pots),
    // .pads = pads,
    // .pad_count = count_of(pads),
    .zone_pads = zone_pads,
    .zone_pad_count = count_of(zone_pads),
    .mcp3008 = &mcp3008,
};

// ---------------------------------------------------------------------------

// GP0-GP22 and GP26-GP28: the pins every Pico, Pico W, Pico 2 and Pico 2 W
// brings out for general use. The others drive the power supply, LED or radio.
static bool gpio_usable(unsigned gpio) {
    return gpio <= 22 || (gpio >= 26 && gpio <= 28);
}

bool layout_mcp3008_ok(void) {
    return layout.mcp3008 && mcp3008_problem(layout.mcp3008) == NULL;
}

static bool mcp3008_pin(unsigned gpio) {
    const mcp3008_def_t *m = layout.mcp3008;
    return layout_mcp3008_ok() && (gpio == m->miso || gpio == m->cs || gpio == m->sck || gpio == m->mosi);
}

static const char *button_problem(size_t i) {
    unsigned gpio = layout.buttons[i].gpio;
    if (!gpio_usable(gpio)) return "not a usable pin";
    if (mcp3008_pin(gpio)) return "pin already used by the MCP3008";
    for (size_t j = 0; j < i; j++)
        if (layout.buttons[j].gpio == gpio) return "pin already used by an earlier button";
    return NULL;
}

// Pots and pads share the analog inputs. `pots_before` and `pads_before` say
// how many of each come ahead of the control being checked.
static const char *adc_problem(uint8_t adc, size_t pots_before, size_t pads_before) {
    if (adc >= MCP3008_CH(0) && adc <= MCP3008_CH(7)) {
        if (!layout.mcp3008) return "an MCP3008 channel, but the layout has no .mcp3008";
        if (!layout_mcp3008_ok()) return "an MCP3008 channel, but the MCP3008 can't be used";
    } else if (adc > 2) {
        return "adc must be 0, 1, 2 (GP26-GP28) or MCP3008_CH(0)-MCP3008_CH(7)";
    } else {
        for (size_t j = 0; j < layout.button_count; j++)
            if (layout.buttons[j].gpio == 26u + adc) return "pin already used by a button";
    }
    for (size_t j = 0; j < pots_before; j++)
        if (layout.pots[j].adc == adc) return "input already used by a pot";
    for (size_t j = 0; j < pads_before; j++)
        if (layout.pads[j].adc == adc) return "input already used by an earlier pad";
    return NULL;
}

static const char *pot_problem(size_t i) {
    return adc_problem(layout.pots[i].adc, i, 0);
}

static const char *pad_problem(size_t i) {
    return adc_problem(layout.pads[i].adc, layout.pot_count, i);
}

size_t layout_zone_inputs(const zone_pad_def_t *z, uint8_t out[ZONE_MAX_INPUTS]) {
    size_t n = 0;
    for (size_t k = 0; k < z->piezos && k < ZONE_MAX_INPUTS; k++) out[n++] = z->adc[k];
    for (size_t k = 0; k < z->extra_count && k < ZONE_MAX_INPUTS && n < ZONE_MAX_INPUTS; k++) {
        uint8_t adc = z->extra[k];
        bool known = false;
        for (size_t j = 0; j < n; j++) known |= out[j] == adc;
        if (!known && !adc_problem(adc, layout.pot_count, layout.pad_count)) out[n++] = adc;
    }
    return n;
}

static const char *zone_pad_problem(size_t i) {
    if (i > 0) return "only one zone pad";
    const zone_pad_def_t *z = &layout.zone_pads[i];
    if (z->piezos < 3 || z->piezos > ZONE_MAX_INPUTS) return "a zone pad needs 3 to ZONE_MAX_INPUTS piezos";
    for (size_t k = 0; k < z->piezos; k++) {
        const char *why = adc_problem(z->adc[k], layout.pot_count, layout.pad_count);
        if (why) return why;
        for (size_t j = 0; j < k; j++)
            if (z->adc[j] == z->adc[k]) return "the same piezo input twice";
    }
    return NULL;
}

bool layout_button_ok(size_t index) {
    return button_problem(index) == NULL;
}

bool layout_pot_ok(size_t index) {
    return pot_problem(index) == NULL;
}

bool layout_pad_ok(size_t index) {
    return pad_problem(index) == NULL;
}

bool layout_zone_pad_ok(size_t index) {
    return zone_pad_problem(index) == NULL;
}

int layout_check(bool print) {
    int problems = 0;
    if (layout.mcp3008 && !layout_mcp3008_ok()) {
        problems++;
        if (print) printf("layout: MCP3008 skipped: %s\n", mcp3008_problem(layout.mcp3008));
    }
    for (size_t i = 0; i < layout.button_count; i++) {
        const char *why = button_problem(i);
        if (!why) continue;
        problems++;
        if (print) printf("layout: button %u (GP%u) skipped: %s\n", (unsigned)i, layout.buttons[i].gpio, why);
    }
    for (size_t i = 0; i < layout.pot_count; i++) {
        const char *why = pot_problem(i);
        if (!why) continue;
        problems++;
        if (print) printf("layout: pot %u (%s) skipped: %s\n", (unsigned)i, analog_name(layout.pots[i].adc), why);
    }
    for (size_t i = 0; i < layout.pad_count; i++) {
        const char *why = pad_problem(i);
        if (!why) continue;
        problems++;
        if (print) printf("layout: pad %u (%s) skipped: %s\n", (unsigned)i, analog_name(layout.pads[i].adc), why);
    }
    for (size_t i = 0; i < layout.zone_pad_count; i++) {
        const char *why = zone_pad_problem(i);
        if (!why) continue;
        problems++;
        if (print) printf("layout: zone pad %u skipped: %s\n", (unsigned)i, why);
    }
    return problems;
}
