#!/usr/bin/env bash
# Build the firmware: tools/build.sh [pico|pico_w|pico2|pico2_w|all] (default all).
# Each board builds in build/<board>/; the file to flash is midi_source.uf2.
set -euo pipefail
cd "$(dirname "$0")/.."

boards=("${1:-all}")
[[ ${boards[0]} == all ]] && boards=(pico pico_w pico2 pico2_w)

for board in "${boards[@]}"; do
    case $board in
        pico|pico_w|pico2|pico2_w) ;;
        *) echo "unknown board '$board' (pico, pico_w, pico2, pico2_w or all)" >&2; exit 2 ;;
    esac
    echo "== $board"
    cmake -S . -B "build/$board" -G Ninja --log-level=WARNING \
        -DPICO_BOARD="$board" -DCMAKE_BUILD_TYPE=Release -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
    cmake --build "build/$board"
    echo "-> build/$board/midi_source.uf2"
done
