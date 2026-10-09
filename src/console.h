#ifndef CONSOLE_H
#define CONSOLE_H

// The USB serial console, core 0 only. printf() writes to it (output from
// core 1 is dropped). Open it with e.g. `screen /dev/cu.usbmodem* 115200`
// and press h for the commands.
//
// Setting the port to 1200 baud reboots the board into BOOTSEL for flashing
// (tools/flash.sh does this).
void console_init(void);
void console_task(void);

#endif
