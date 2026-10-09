// USB identity: one composite device with a MIDI port and a console (CDC).

#include <string.h>

#include "pico/unique_id.h"
#include "tusb.h"

// Development IDs, following TinyUSB's example scheme (0x4000 | CDC bit 0 |
// MIDI bit 3). Before giving units to other people, ask Raspberry Pi for a free
// PID under their VID: https://github.com/raspberrypi/usb-pid
#define USB_VID 0xCafe
#define USB_PID 0x4009

enum {
    STRID_LANGID = 0,
    STRID_MANUFACTURER,
    STRID_PRODUCT,
    STRID_SERIAL,
    STRID_MIDI,
    STRID_CDC,
};

static const tusb_desc_device_t desc_device = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0200,
    // Interface Association Descriptors, required by the CDC function.
    .bDeviceClass = TUSB_CLASS_MISC,
    .bDeviceSubClass = MISC_SUBCLASS_COMMON,
    .bDeviceProtocol = MISC_PROTOCOL_IAD,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor = USB_VID,
    .idProduct = USB_PID,
    .bcdDevice = 0x0100,
    .iManufacturer = STRID_MANUFACTURER,
    .iProduct = STRID_PRODUCT,
    .iSerialNumber = STRID_SERIAL,
    .bNumConfigurations = 1,
};

uint8_t const *tud_descriptor_device_cb(void) {
    return (uint8_t const *)&desc_device;
}

enum {
    ITF_NUM_MIDI = 0,
    ITF_NUM_MIDI_STREAMING,
    ITF_NUM_CDC,
    ITF_NUM_CDC_DATA,
    ITF_NUM_TOTAL,
};

#define EPNUM_MIDI_OUT  0x01
#define EPNUM_MIDI_IN   0x81
#define EPNUM_CDC_NOTIF 0x82
#define EPNUM_CDC_OUT   0x03
#define EPNUM_CDC_IN    0x83

#define CONFIG_TOTAL_LEN (TUD_CONFIG_DESC_LEN + TUD_MIDI_DESC_LEN + TUD_CDC_DESC_LEN)

static const uint8_t desc_configuration[] = {
    TUD_CONFIG_DESCRIPTOR(1, ITF_NUM_TOTAL, 0, CONFIG_TOTAL_LEN, 0x00, 100),
    TUD_MIDI_DESCRIPTOR(ITF_NUM_MIDI, STRID_MIDI, EPNUM_MIDI_OUT, EPNUM_MIDI_IN, 64),
    TUD_CDC_DESCRIPTOR(ITF_NUM_CDC, STRID_CDC, EPNUM_CDC_NOTIF, 8, EPNUM_CDC_OUT, EPNUM_CDC_IN, 64),
};

uint8_t const *tud_descriptor_configuration_cb(uint8_t index) {
    return desc_configuration;
}

static const char *const strings[] = {
    [STRID_MANUFACTURER] = "Vibetuned",
    [STRID_PRODUCT] = "midi-source",
    [STRID_MIDI] = "midi-source MIDI",
    [STRID_CDC] = "midi-source console",
};

#define MAX_STRING_CHARS 32
static uint16_t desc_string[1 + MAX_STRING_CHARS];

uint16_t const *tud_descriptor_string_cb(uint8_t index, uint16_t langid) {
    size_t len;
    if (index == STRID_LANGID) {
        desc_string[1] = 0x0409;  // English (US)
        len = 1;
    } else {
        // The serial number is the flash chip's unique ID, so hosts can tell
        // several midi-source boards apart.
        char serial[2 * PICO_UNIQUE_BOARD_ID_SIZE_BYTES + 1];
        const char *s;
        if (index == STRID_SERIAL) {
            pico_get_unique_board_id_string(serial, sizeof serial);
            s = serial;
        } else if (index < count_of(strings) && strings[index]) {
            s = strings[index];
        } else {
            return NULL;
        }
        len = strlen(s);
        if (len > MAX_STRING_CHARS) len = MAX_STRING_CHARS;
        for (size_t i = 0; i < len; i++) desc_string[1 + i] = (uint8_t)s[i];
    }
    desc_string[0] = (uint16_t)((TUSB_DESC_STRING << 8) | (2 * len + 2));
    return desc_string;
}
