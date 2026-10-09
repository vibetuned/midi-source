#include "console.h"

#include <stdio.h>

#include "analog.h"
#include "capture.h"
#include "inputs/pads.h"
#include "inputs/pots.h"
#include "inputs/zones.h"
#include "instrument.h"
#include "layout.h"
#include "midi_out.h"
#include "pico/bootrom.h"
#include "pico/stdio/driver.h"
#include "pico/stdlib.h"
#include "stream.h"
#include "tusb.h"

#define BOOTSEL_BAUD 1200

// How long a print may wait for USB to drain a full FIFO before the rest is
// dropped; a host that stops reading must not stall MIDI for long.
#define CDC_DRAIN_TIMEOUT_MS 20

static void cdc_out_chars(const char *buf, int len) {
    // TinyUSB runs on core 0 and is not safe to call from core 1.
    if (get_core_num() != 0 || !tud_cdc_connected()) return;
    absolute_time_t give_up = make_timeout_time_ms(CDC_DRAIN_TIMEOUT_MS);
    while (len > 0) {
        uint32_t n = tud_cdc_write(buf, (uint32_t)len);
        buf += n;
        len -= (int)n;
        if (n == 0) {
            if (time_reached(give_up)) break;
            tud_cdc_write_flush();
            tud_task();  // prints come from the main loop, never from inside tud_task
        }
    }
}

static void cdc_out_flush(void) {
    if (get_core_num() == 0) tud_cdc_write_flush();
}

static stdio_driver_t cdc_stdio = {
    .out_chars = cdc_out_chars,
    .out_flush = cdc_out_flush,
#if PICO_STDIO_ENABLE_CRLF_SUPPORT
    .crlf_enabled = true,
#endif
};

void console_init(void) {
    stdio_set_driver_enabled(&cdc_stdio, true);
}

static bool bootsel_requested;

// The "1200-baud touch": the same convention picotool and Arduino use. The
// reboot waits for console_task(): rebooting here, before the host's request
// is acknowledged, could leave macOS or a hub unable to see the board for a
// long while.
void tud_cdc_line_coding_cb(uint8_t itf, cdc_line_coding_t const *coding) {
    if (coding->bit_rate == BOOTSEL_BAUD) bootsel_requested = true;
}

// Finish what USB is sending, detach cleanly so the host sees the device
// leave, then reboot into BOOTSEL.
static void reboot_to_bootsel(void) {
    tud_cdc_write_flush();
    absolute_time_t until = make_timeout_time_ms(50);
    while (!time_reached(until)) tud_task();
    tud_disconnect();
    sleep_ms(100);
    reset_usb_boot(0, 0);
}

static void print_help(void) {
    printf("midi-source commands:\n"
           "  s  stats\n"
           "  m  MIDI monitor on/off\n"
           "  p  pot, pad and zone pad readings\n"
           "  c  calibrate pots (press, turn each end to end, press again)\n"
           "  w  record the next pad hit, 200 ms of its envelope\n"
           "  x  capture the next hit: raw samples of every analog input (tools/hits.py)\n"
           "  V  start the live stream for tools/scope.py (v stops it)\n"
           "  l  check the layout\n"
           "  b  reboot into BOOTSEL (for flashing)\n"
           "  h  this help\n");
}

static void print_stats(void) {
    midi_stats_t m = midi_stats();
    instrument_stats_t s = instrument_stats();
    printf("uptime        %lu s\n", (unsigned long)(to_ms_since_boot(get_absolute_time()) / 1000));
    printf("controls      %u buttons, %u pots, %u pads, %u zone pads\n", (unsigned)layout.button_count,
           (unsigned)layout.pot_count, (unsigned)layout.pad_count, (unsigned)layout.zone_pad_count);
    static const char *const adc_names[ANALOG_ADCS] = {"ADC", "MCP3008"};
    for (int adc = 0; adc < ANALOG_ADCS; adc++) {
        analog_stats_t a = analog_stats(adc);
        if (adc == ANALOG_MCP3008 && !a.inputs) continue;
        printf("%-13s %lu inputs at %lu samples/s each (measured %lu/s in total), %u bits, %lu overruns\n",
               adc_names[adc], (unsigned long)a.inputs, (unsigned long)a.input_rate, (unsigned long)a.measured,
               a.bits, (unsigned long)a.overruns);
        if (adc == ANALOG_MCP3008) {
            mcp3008_activity_t w = mcp3008_watch(layout.mcp3008, 2000);
            printf("MCP3008 pins  changes in 2 ms: CS %lu, CLK %lu, Din %lu, Dout %lu%s\n", (unsigned long)w.cs,
                   (unsigned long)w.sck, (unsigned long)w.mosi, (unsigned long)w.miso,
                   w.cs && w.sck && !w.miso && w.miso_high ? "  <- Dout never moves: the chip isn't answering "
                                                             "(check its power, grounds and the four SPI wires)" : "");
        }
    }
    printf("scan          every %u us, longest %lu us, %lu overruns\n", INSTRUMENT_SCAN_US,
           (unsigned long)s.scan_max_us, (unsigned long)s.overruns);
    printf("MIDI out      %lu sent, %lu discarded (no host), %lu dropped (queue full)\n",
           (unsigned long)m.sent, (unsigned long)m.discarded, (unsigned long)m.dropped);
    printf("MIDI in       %lu\n", (unsigned long)m.received);
    printf("stream        %s, %lu frames dropped\n", stream_active() ? "on" : "off",
           (unsigned long)stream_dropped());
}

static void print_banner(void) {
    printf("\nmidi-source on %s. Press h for help.\n", PICO_BOARD);
    layout_check(true);
}

static void handle(int c) {
    switch (c) {
    case 'h': case '?': print_help(); break;
    case 's': print_stats(); break;
    case 'p': pots_print(); pads_print(); zones_print(); break;
    case 'c': pots_calibrate(); break;
    case 'w': pads_capture_arm(); break;
    case 'x': capture_arm(); break;
    case 'V': stream_start(); break;
    case 'v': stream_stop(); break;
    case 'm':
        midi_set_monitor(!midi_monitor());
        printf("MIDI monitor %s\n", midi_monitor() ? "on" : "off");
        break;
    case 'l':
        if (layout_check(true) == 0) printf("layout OK\n");
        break;
    case 'b':
        printf("rebooting into BOOTSEL\n");
        reboot_to_bootsel();
        break;
    case '\r': case '\n': break;
    default: printf("unknown command '%c', press h for help\n", c); break;
    }
}

void console_task(void) {
    if (bootsel_requested) reboot_to_bootsel();

    static bool was_connected;
    bool connected = tud_cdc_connected();
    if (connected && !was_connected) print_banner();
    was_connected = connected;

    while (tud_cdc_available()) handle(tud_cdc_read_char());
    if (connected) {  // recordings wait until someone can read them
        pads_capture_poll();
        capture_poll();
    }
    tud_cdc_write_flush();
}
