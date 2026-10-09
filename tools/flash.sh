#!/usr/bin/env bash
# Flash over USB: tools/flash.sh <pico|pico_w|pico2|pico2_w> [serial]
#
# A board already in BOOTSEL is flashed directly. A running midi-source board
# is first sent to BOOTSEL by opening its console port at 1200 baud. With
# several boards connected, pass the serial number shown by tools/boards.py.
set -euo pipefail
cd "$(dirname "$0")/.."

board=${1:?usage: tools/flash.sh <pico|pico_w|pico2|pico2_w> [serial]}
serial=${2:-}
uf2="build/$board/midi_source.uf2"
picotool=${PICOTOOL:-$HOME/.pico-sdk/picotool/2.3.1/picotool/picotool}

[[ -f $uf2 ]] || { echo "$uf2 not found: run tools/build.sh $board first" >&2; exit 1; }

select=()
[[ -n $serial ]] && select=(--ser "$serial")
in_bootsel() { "$picotool" info ${select[@]+"${select[@]}"} >/dev/null 2>&1; }

if ! in_bootsel; then
    port="" found=0
    while read -r ser dev; do
        [[ -z $serial || $ser == "$serial" ]] || continue
        port=$dev
        found=$((found + 1))
    done < <(python3 tools/boards.py --running)

    if ((found == 0)); then
        echo "No running midi-source board${serial:+ with serial $serial}, and none in BOOTSEL." >&2
        echo "Hold BOOTSEL while plugging the board in, then run this again." >&2
        exit 1
    fi
    if ((found > 1)); then
        echo "Several midi-source boards are connected; pass the serial of the one to flash:" >&2
        python3 tools/boards.py >&2
        exit 1
    fi

    echo "Rebooting $port into BOOTSEL"
    if [[ $(uname) == Darwin ]]; then stty -f "$port" 1200 || true; else stty -F "$port" 1200 || true; fi
    # Usually about a second; macOS can take much longer to show the new device.
    for _ in $(seq 200); do in_bootsel && break; sleep 0.1; done
    in_bootsel || { echo "The board did not show up in BOOTSEL within 20 s; hold BOOTSEL while plugging it in." >&2; exit 1; }
fi

"$picotool" load -x ${select[@]+"${select[@]}"} "$uf2"
