#include "mcp3008.h"

#include "hardware/clocks.h"
#include "hardware/dma.h"
#include "hardware/pio.h"
#include "mcp3008.pio.h"
#include "pico/stdlib.h"

// The fastest clocks the datasheets give at 2.7 V (3.6 and 2 MHz at 5 V). At
// 3.3 V an MCP3008 converts about 70,000 times a second, an MCP3208 47,000.
#define MCP3008_CLOCK_HZ 1350000
#define MCP3208_CLOCK_HZ 1000000

#define MAX_CHANNELS 8

// One command per slot (mcp3008.pio), read by DMA in a ring that wraps on an
// address boundary of its own size.
static uint32_t commands[MAX_CHANNELS] __attribute__((aligned(MAX_CHANNELS * sizeof(uint32_t))));

const char *mcp3008_problem(const mcp3008_def_t *def) {
    if (def->bits && def->bits != 10 && def->bits != 12) return "bits must be 10 (MCP3008) or 12 (MCP3208)";
    if (def->sck != def->cs + 1) return "sck must be the GPIO after cs (the state machine drives them together)";
    uint8_t pins[4] = {def->miso, def->cs, def->sck, def->mosi};
    for (int i = 0; i < 4; i++) {
        if (pins[i] > 22) return "its pins must be among GP0-GP22";
        for (int j = 0; j < i; j++)
            if (pins[i] == pins[j]) return "the same pin twice";
    }
    return NULL;
}

static unsigned log2_of(size_t n) {
    unsigned k = 0;
    while ((1u << k) < n) k++;
    return k;
}

bool mcp3008_start(const mcp3008_def_t *def, const uint8_t *channels, size_t count, mcp3008_source_t *out) {
    if (mcp3008_problem(def) || count == 0 || count > MAX_CHANNELS || (count & (count - 1))) return false;
    unsigned bits = def->bits ? def->bits : 10;

    uint lowest = MIN(MIN(def->miso, def->cs), def->mosi), highest = MAX(MAX(def->miso, def->sck), def->mosi);
    PIO pio;
    uint sm, offset;
    if (!pio_claim_free_sm_and_add_program_for_gpio_range(&mcp3008_program, &pio, &sm, &offset, lowest,
                                                          highest - lowest + 1, true))
        return false;

    pio_sm_config c = mcp3008_program_get_default_config(offset);
    sm_config_set_out_pins(&c, def->mosi, 1);
    sm_config_set_in_pins(&c, def->miso);
    sm_config_set_sideset_pins(&c, def->cs);       // CS, then CLK
    sm_config_set_out_shift(&c, false, false, 32);  // MSB first, pulled by hand
    sm_config_set_in_shift(&c, false, false, 32);   // MSB first, pushed by hand
    // Two cycles per clock, no faster than the chip's clock.
    uint32_t sys = clock_get_hz(clk_sys), clock = bits == 12 ? MCP3208_CLOCK_HZ : MCP3008_CLOCK_HZ;
    uint32_t div = (sys + 2 * clock - 1) / (2 * clock);
    sm_config_set_clkdiv_int_frac8(&c, div, 0);

    uint32_t outputs = (1u << def->mosi) | (1u << def->cs) | (1u << def->sck);
    pio_sm_set_pins_with_mask(pio, sm, 1u << def->cs, outputs);  // CS high, CLK and Din low
    pio_sm_set_pindirs_with_mask(pio, sm, outputs, outputs | (1u << def->miso));
    pio_gpio_init(pio, def->mosi);
    pio_gpio_init(pio, def->cs);
    pio_gpio_init(pio, def->sck);
    gpio_init(def->miso);
    gpio_pull_up(def->miso);  // a chip that never answers reads full scale, which a real input never does
    pio_sm_init(pio, sm, offset, &c);

    for (size_t i = 0; i < count; i++)
        commands[i] = (3u << 30) | ((uint32_t)channels[i] << 27) | ((bits - 1) << 20);

    // Two DMA channels chained to each other feed the commands for ever. Each
    // run is a whole number of rounds, so the next starts on the first channel.
    int a = dma_claim_unused_channel(true), b = dma_claim_unused_channel(true);
    int pair[2][2] = {{a, b}, {b, a}};
    for (int i = 0; i < 2; i++) {
        dma_channel_config cfg = dma_channel_get_default_config(pair[i][0]);
        channel_config_set_transfer_data_size(&cfg, DMA_SIZE_32);
        channel_config_set_read_increment(&cfg, true);
        channel_config_set_write_increment(&cfg, false);
        channel_config_set_ring(&cfg, false, log2_of(count * sizeof(uint32_t)));
        channel_config_set_dreq(&cfg, pio_get_dreq(pio, sm, true));
        channel_config_set_chain_to(&cfg, pair[i][1]);
        dma_channel_configure(pair[i][0], &cfg, &pio->txf[sm], commands, (uint32_t)count << 20, false);
    }
    dma_channel_start(a);
    pio_sm_set_enabled(pio, sm, true);  // results wait in the RX FIFO until their DMA starts

    *out = (mcp3008_source_t){
        .fifo = &pio->rxf[sm],
        .dreq = pio_get_dreq(pio, sm, false),
        .rate = sys / div / (18 + 2 * bits),
        .bits = (uint8_t)bits,
    };
    return true;
}

mcp3008_activity_t mcp3008_watch(const mcp3008_def_t *def, uint32_t us) {
    mcp3008_activity_t a = {0};
    uint32_t pins[4] = {def->cs, def->sck, def->mosi, def->miso};
    uint32_t *counts[4] = {&a.cs, &a.sck, &a.mosi, &a.miso};
    uint32_t before = gpio_get_all(), start = time_us_32();
    while (time_us_32() - start < us) {
        uint32_t now = gpio_get_all(), changed = now ^ before;
        if (changed)
            for (int i = 0; i < 4; i++) *counts[i] += (changed >> pins[i]) & 1;
        before = now;
    }
    a.miso_high = (before >> def->miso) & 1;
    return a;
}
