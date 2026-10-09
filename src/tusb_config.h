#ifndef TUSB_CONFIG_H
#define TUSB_CONFIG_H

// The SDK supplies CFG_TUSB_MCU and CFG_TUSB_OS on the command line.

#define CFG_TUSB_RHPORT0_MODE   OPT_MODE_DEVICE
#define CFG_TUD_ENDPOINT0_SIZE  64

#define CFG_TUD_CDC     1   // console: logs, MIDI monitor, 1200-baud reboot to BOOTSEL
#define CFG_TUD_MIDI    1
#define CFG_TUD_MSC     0
#define CFG_TUD_HID     0
#define CFG_TUD_VENDOR  0

#define CFG_TUD_CDC_RX_BUFSIZE  64
#define CFG_TUD_CDC_TX_BUFSIZE  1024  // the MIDI monitor prints in bursts
#define CFG_TUD_CDC_EP_BUFSIZE  64

// The TX FIFO absorbs bursts (a chord, or every MPE voice moving at once);
// 4 bytes per USB-MIDI packet.
#define CFG_TUD_MIDI_RX_BUFSIZE 128
#define CFG_TUD_MIDI_TX_BUFSIZE 512

#endif
