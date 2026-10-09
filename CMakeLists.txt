cmake_minimum_required(VERSION 3.13)

# == DO NOT EDIT THE FOLLOWING LINES for the Raspberry Pi Pico VS Code Extension to work ==
if(WIN32)
    set(USERHOME $ENV{USERPROFILE})
else()
    set(USERHOME $ENV{HOME})
endif()
set(sdkVersion 2.3.1)
set(toolchainVersion 15_2_Rel1)
set(picotoolVersion 2.3.1)
set(picoVscode ${USERHOME}/.pico-sdk/cmake/pico-vscode.cmake)
if (EXISTS ${picoVscode})
    include(${picoVscode})
endif()
# ====================================================================================
# pico, pico_w, pico2 or pico2_w. tools/build.sh passes it per build directory.
set(PICO_BOARD pico CACHE STRING "Board type")

include(pico_sdk_import.cmake)

project(midi-source C CXX ASM)
set(CMAKE_C_STANDARD 11)

pico_sdk_init()

set(MIDI_SOURCE_SOURCES
    src/main.c
    src/usb_descriptors.c
    src/midi_out.c
    src/console.c
    src/led.c
    src/instrument.c
    src/layout.c
    src/analog.c
    src/mcp3008.c
    src/stream.c
    src/capture.c
    src/inputs/buttons.c
    src/inputs/pots.c
    src/inputs/pads.c
    src/inputs/zones.c
)

add_executable(midi_source ${MIDI_SOURCE_SOURCES})
pico_generate_pio_header(midi_source ${CMAKE_CURRENT_LIST_DIR}/src/mcp3008.pio)

# Warnings for our sources only; the SDK and TinyUSB compile into the same
# target and are not -Wextra clean.
set_source_files_properties(${MIDI_SOURCE_SOURCES} PROPERTIES
    COMPILE_OPTIONS "-Wall;-Wextra;-Wno-unused-parameter")

pico_set_program_name(midi_source "midi-source")
pico_set_program_description(midi_source "USB MIDI controller")
pico_set_program_version(midi_source "0.1.0")

target_include_directories(midi_source PRIVATE src)

target_link_libraries(midi_source PRIVATE
    pico_stdlib
    pico_multicore
    pico_unique_id
    hardware_adc
    hardware_dma
    hardware_pio
    tinyusb_device
)

# Pico W / Pico 2 W: the LED hangs off the wireless chip.
if (PICO_CYW43_SUPPORTED)
    target_link_libraries(midi_source PRIVATE pico_cyw43_arch_none)
endif()

# USB carries our own MIDI + console device, not the SDK's stdio-over-USB.
pico_enable_stdio_usb(midi_source 0)
pico_enable_stdio_uart(midi_source 0)

pico_add_extra_outputs(midi_source)
