#include "inputs/buttons.h"

#include "layout.h"
#include "midi_out.h"
#include "pico/stdlib.h"

// After an edge the pin is ignored this long while the contacts bounce. The
// first edge is reported at once, so debouncing adds no latency.
#define BUTTON_DEBOUNCE_US 5000

#define BUTTON_DEFAULT_VELOCITY 100
#define BUTTON_RELEASE_VELOCITY 64  // the MIDI spec's value when velocity isn't sensed

// One per usable GPIO is the most a valid layout can have.
#define MAX_BUTTONS 32

typedef struct {
    bool enabled;
    bool down;
    bool latched;  // BUTTON_CC_TOGGLE
    uint32_t changed_us;
} button_state_t;

static button_state_t state[MAX_BUTTONS];
static size_t count;

void buttons_init(void) {
    count = MIN(layout.button_count, MAX_BUTTONS);
    for (size_t i = 0; i < count; i++) {
        if (!layout_button_ok(i)) continue;
        unsigned gpio = layout.buttons[i].gpio;
        gpio_init(gpio);
        gpio_set_dir(gpio, GPIO_IN);
        gpio_pull_up(gpio);
        state[i].enabled = true;
    }
    busy_wait_us(100);  // let the pull-ups charge the pins before the first read
}

static void send(const button_def_t *def, button_state_t *st) {
    uint8_t ch = layout_channel(def->channel);
    switch (def->kind) {
    case BUTTON_NOTE:
        if (st->down)
            midi_note_on(ch, def->number, def->velocity ? def->velocity : BUTTON_DEFAULT_VELOCITY);
        else
            midi_note_off(ch, def->number, BUTTON_RELEASE_VELOCITY);
        break;
    case BUTTON_CC:
        midi_cc(ch, def->number, st->down ? 127 : 0);
        break;
    case BUTTON_CC_TOGGLE:
        if (!st->down) break;
        st->latched = !st->latched;
        midi_cc(ch, def->number, st->latched ? 127 : 0);
        break;
    case BUTTON_PROGRAM:
        if (st->down) midi_program_change(ch, def->number);
        break;
    }
}

void buttons_scan(uint32_t now_us) {
    uint32_t pins = gpio_get_all();
    for (size_t i = 0; i < count; i++) {
        button_state_t *st = &state[i];
        if (!st->enabled) continue;
        const button_def_t *def = &layout.buttons[i];
        bool down = !(pins & (1u << def->gpio));  // pressed pulls the pin to GND
        if (down == st->down || now_us - st->changed_us < BUTTON_DEBOUNCE_US) continue;
        st->down = down;
        st->changed_us = now_us;
        send(def, st);
    }
}
