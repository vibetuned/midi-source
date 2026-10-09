#include "analog.h"

#include "hardware/adc.h"
#include "hardware/dma.h"
#include "pico/stdlib.h"

// 8 KB per ADC: about 8 ms of samples at the Pico ADC's 500 kS/s, about 60 ms
// of an MCP3008's. The scan reads them every 1 ms.
#define RING_BITS 13
#define RING_SAMPLES ((1u << RING_BITS) / sizeof(uint16_t))

#define MAX_SLOTS 8
#define FILLER 7  // the input number a filler slot hands its samples to (none uses it)

typedef struct {
    uint16_t *ring;             // the DMA write ring; wraps on an address boundary of its own size
    uint16_t *split;            // this scan's samples, grouped by slot
    uint8_t order[MAX_SLOTS];   // input sampled in each slot of a round
    uint32_t slots;             // inputs per round, a power of two; 0 = not running
    uint32_t rate;              // samples per second, all slots together
    uint32_t raw_q16;           // microseconds per sample, all slots together, x 65536
    uint8_t bits, shift;        // the ADC's resolution, and how far it is from 12 bits
    int dma_a, dma_b;
    uint32_t tail;              // next ring index to read
    uint32_t round;             // rounds handed out so far: every input's next block starts here
    uint32_t ahead;             // samples already in the ring past them, at the latest scan
    uint32_t scan_time;         // when the latest scan read the ring
    volatile uint32_t overruns, measured;
    uint32_t window_start, window_samples;
} source_t;

static uint16_t ring_pico[RING_SAMPLES] __attribute__((aligned(1u << RING_BITS)));
static uint16_t ring_mcp[RING_SAMPLES] __attribute__((aligned(1u << RING_BITS)));
static uint16_t split_pico[RING_SAMPLES], split_mcp[RING_SAMPLES];
static source_t sources[ANALOG_ADCS] = {
    {.ring = ring_pico, .split = split_pico, .bits = 12},
    {.ring = ring_mcp, .split = split_mcp},
};
static analog_block_t blocks[ANALOG_IDS];

static source_t *source(uint8_t input) {
    return &sources[analog_adc(input)];
}

// Two DMA channels chained to each other fill the ring for ever, reading the
// ADC's FIFO at each request; each write address wraps back to the start, so
// each run begins at index 0.
static void start_ring(source_t *src, const volatile void *fifo, unsigned dreq) {
    src->dma_a = dma_claim_unused_channel(true);
    src->dma_b = dma_claim_unused_channel(true);
    int channels[2][2] = {{src->dma_a, src->dma_b}, {src->dma_b, src->dma_a}};
    for (int i = 0; i < 2; i++) {
        dma_channel_config c = dma_channel_get_default_config(channels[i][0]);
        channel_config_set_transfer_data_size(&c, DMA_SIZE_16);
        channel_config_set_read_increment(&c, false);
        channel_config_set_write_increment(&c, true);
        channel_config_set_ring(&c, true, RING_BITS);
        channel_config_set_dreq(&c, dreq);
        channel_config_set_chain_to(&c, channels[i][1]);
        dma_channel_configure(channels[i][0], &c, src->ring, fifo, RING_SAMPLES, false);
    }
    dma_channel_start(src->dma_a);
}

static void set_rate(source_t *src, uint32_t rate) {
    src->rate = rate;
    src->raw_q16 = (uint32_t)((1000000ull << 16) / rate);
    src->shift = (uint8_t)(12 - src->bits);
    src->window_start = time_us_32();
}

static void init_pico(uint32_t input_mask) {
    source_t *src = &sources[ANALOG_PICO];
    input_mask &= 0x7;  // GP26-GP28; GP29 is the Pico's VSYS sense (and the W's radio)
    if (!input_mask) return;

    adc_init();
    for (uint8_t i = 0; i < 3; i++) {
        if (input_mask & (1u << i)) {
            adc_gpio_init(26 + i);
            src->order[src->slots++] = i;
        }
    }
    // A sample's input comes from its ring index, which needs a power-of-two
    // round. Three inputs get the temperature sensor as a fourth, ignored.
    if (src->slots == 3) {
        input_mask |= 1u << ADC_TEMPERATURE_CHANNEL_NUM;
        src->order[src->slots++] = ADC_TEMPERATURE_CHANNEL_NUM;
    }

    adc_select_input(src->order[0]);  // the round starts on the lowest input
    adc_set_round_robin(input_mask);
    // 0 runs conversions back to back, 96 cycles of the 48 MHz ADC clock each:
    // 500 kS/s. A divider of exactly 96 cycles halves that, since each trigger
    // lands while the previous conversion is still finishing.
    adc_set_clkdiv(0);
    adc_fifo_setup(true, true, 1, false, false);  // 12-bit samples, DMA request per sample
    adc_fifo_drain();
    start_ring(src, &adc_hw->fifo, DREQ_ADC);
    adc_run(true);
    set_rate(src, ANALOG_SAMPLE_RATE);
}

static void init_mcp3008(uint32_t channel_mask, const mcp3008_def_t *def) {
    source_t *src = &sources[ANALOG_MCP3008];
    uint8_t channels[MAX_SLOTS];
    size_t n = 0;
    for (uint8_t ch = 0; ch < 8; ch++) {
        if (channel_mask & (1u << ch)) {
            channels[n] = ch;
            src->order[n++] = (uint8_t)MCP3008_CH(ch);
        }
    }
    if (!n) return;
    size_t slots = 1;
    while (slots < n) slots <<= 1;
    for (size_t s = n; s < MAX_SLOTS; s++) {  // fillers up to `slots`: the last channel again, ignored
        channels[s] = channels[n - 1];
        src->order[s] = FILLER;
    }
    mcp3008_source_t chip;
    if (!mcp3008_start(def, channels, slots, &chip)) return;
    src->slots = (uint32_t)slots;
    src->bits = chip.bits;
    start_ring(src, chip.fifo, chip.dreq);
    set_rate(src, chip.rate);
}

void analog_init(uint32_t input_mask, const mcp3008_def_t *mcp3008) {
    init_pico(input_mask);
    if (mcp3008) init_mcp3008(input_mask >> MCP3008_CH(0), mcp3008);
}

static uint32_t head(const source_t *src) {
    int active = dma_channel_is_busy(src->dma_a) ? src->dma_a : src->dma_b;
    uintptr_t addr = dma_channel_hw_addr(active)->write_addr;
    return (uint32_t)((addr - (uintptr_t)src->ring) / sizeof(uint16_t)) & (RING_SAMPLES - 1);
}

static void scan(source_t *src) {
    uint32_t h = head(src);
    src->scan_time = time_us_32();
    uint32_t n = (h - src->tail) & (RING_SAMPLES - 1);
    if (n > RING_SAMPLES * 3 / 4) src->overruns++;  // the oldest may already be overwritten
    uint32_t whole = n & ~(src->slots - 1);         // whole rounds only, so every input gets the same count

    uint32_t per_input = whole / src->slots, stride = RING_SAMPLES / src->slots;
    for (uint32_t s = 0; s < src->slots; s++) {
        analog_block_t *b = &blocks[src->order[s]];
        b->first = src->round;
        b->samples = &src->split[s * stride];
        b->count = per_input;
    }
    for (uint32_t k = 0; k < per_input; k++)
        for (uint32_t s = 0; s < src->slots; s++)
            src->split[s * stride + k] = (uint16_t)(src->ring[(src->tail + k * src->slots + s) & (RING_SAMPLES - 1)]
                                                    << src->shift);
    src->tail = (src->tail + whole) & (RING_SAMPLES - 1);
    src->round += per_input;
    src->ahead = n - whole;

    src->window_samples += whole;
    uint32_t elapsed = src->scan_time - src->window_start;
    if (elapsed >= 1000000) {
        src->measured = (uint32_t)((uint64_t)src->window_samples * 1000000 / elapsed);
        src->window_start += elapsed;
        src->window_samples = 0;
    }
}

void analog_scan(void) {
    for (int a = 0; a < ANALOG_ADCS; a++)
        if (sources[a].slots) scan(&sources[a]);
}

const analog_block_t *analog_block(uint8_t input) {
    return &blocks[input];
}

uint8_t analog_slot(uint8_t input) {
    const source_t *src = source(input);
    for (uint32_t s = 0; s < src->slots; s++)
        if (src->order[s] == input) return (uint8_t)s;
    return 0;
}

uint32_t analog_rate(uint8_t input) {
    const source_t *src = source(input);
    return src->slots ? src->rate / src->slots : 0;
}

uint32_t analog_period_q16(uint8_t input) {
    const source_t *src = source(input);
    return src->raw_q16 * src->slots;
}

// A sample's place in its ADC's stream, in samples (all slots) after the
// start of the round the next scan hands out first.
static int32_t position(const source_t *src, uint8_t input, uint32_t index) {
    return (int32_t)(index - src->round) * (int32_t)src->slots + analog_slot(input);
}

// The newest sample in the ring was taken about one sample period before the
// scan read it.
uint32_t analog_time(uint8_t input, uint32_t index) {
    const source_t *src = source(input);
    int64_t back = (int64_t)src->ahead - position(src, input, index);  // samples before the head
    return src->scan_time - (uint32_t)((back * src->raw_q16) >> 16);
}

uint32_t analog_index(uint8_t input, uint32_t t_us) {
    const source_t *src = source(input);
    if (!src->slots) return 0;
    // In samples x 65536: how far before the head t_us is, and after the round's start.
    int64_t back = ((int64_t)(int32_t)(src->scan_time - t_us) << 32) / src->raw_q16;
    int64_t from_round = ((int64_t)((int32_t)src->ahead - analog_slot(input)) << 16) - back;
    // The nearest round: shifting down rounds towards minus infinity, like a floor.
    int32_t rounds = (int32_t)((from_round + ((int64_t)src->slots << 15)) >> (16 + __builtin_ctz(src->slots)));
    return src->round + (uint32_t)rounds;
}

bool analog_history(uint8_t input, uint32_t first, size_t count, uint16_t *out) {
    const source_t *src = source(input);
    if (!src->slots) return false;
    uint32_t end = src->round;  // one past the newest handed out
    // The DMA keeps writing over the oldest samples (about 1 ms between scans):
    // a quarter of the ring is kept clear of it.
    uint32_t oldest = end - (RING_SAMPLES / src->slots) * 3 / 4;
    if ((int32_t)(first - oldest) < 0 || (int32_t)(end - (first + (uint32_t)count)) < 0) return false;
    uint32_t s = analog_slot(input);
    for (size_t k = 0; k < count; k++)
        out[k] = (uint16_t)(src->ring[((first + (uint32_t)k) * src->slots + s) & (RING_SAMPLES - 1)] << src->shift);
    return true;
}

size_t analog_inputs(uint8_t out[ANALOG_MAX_INPUTS]) {
    size_t n = 0;
    for (int a = 0; a < ANALOG_ADCS; a++) {  // each ADC's slots go in ascending order, fillers last
        const source_t *src = &sources[a];
        for (uint32_t s = 0; s < src->slots; s++) {
            uint8_t input = src->order[s];
            if (input <= 2 || input >= MCP3008_CH(0)) out[n++] = input;  // not a filler
        }
    }
    return n;
}

const char *analog_name(uint8_t input) {
    static const char *const names[ANALOG_IDS] = {
        "GP26", "GP27", "GP28", "GP29", "temp", "?", "?", "?",
        "CH0", "CH1", "CH2", "CH3", "CH4", "CH5", "CH6", "CH7",
    };
    return input < ANALOG_IDS ? names[input] : "?";
}

analog_stats_t analog_stats(int adc) {
    const source_t *src = &sources[adc];
    return (analog_stats_t){
        .inputs = src->slots,
        .input_rate = src->slots ? src->rate / src->slots : 0,
        .overruns = src->overruns,
        .measured = src->measured,
        .bits = src->bits,
    };
}
