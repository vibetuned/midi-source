"""Reusable hardware blocks for midi-source, written with SKiDL.

Each block is a function that adds its parts to the current circuit and
connects them to the nets it is given. Every block pairs with a firmware
module: its docstring says which entry in src/layout.c drives it.

Importing this package points SKiDL at the libraries of the installed KiCad.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

_KICAD = "/Applications/KiCad/KiCad.app/Contents/SharedSupport"
os.environ.setdefault("KICAD10_FOOTPRINT_DIR", f"{_KICAD}/footprints")
# SKiDL sets up every KiCad version it knows on import and warns for each one
# without a library path; they all get the installed KiCad 10 libraries.
for _version in ("", "5", "6", "7", "8", "9", "10"):
    os.environ.setdefault(f"KICAD{_version}_SYMBOL_DIR", f"{_KICAD}/symbols")

from skidl import KICAD10, generate_netlist, set_default_tool  # noqa: E402
from skidl.logger import stop_log_file_output  # noqa: E402

set_default_tool(KICAD10)
stop_log_file_output()  # no <script>.log / .erc files next to the designs

BUILD = Path(__file__).resolve().parent.parent / "build"


def write_kicad(name, title, pico, blocks):
    """Write the circuit to build/<name>/: a netlist for the PCB editor (from
    SKiDL), one readable schematic sheet (from the blocks' drawings) and its
    PDF. Then check the drawing: KiCad's netlist of the sheet must connect
    exactly the pins SKiDL connects, and KiCad's ERC must find no errors."""
    from .drawing import Sheet, layout
    from .sexp import child, children, parse

    out = BUILD / name
    out.mkdir(parents=True, exist_ok=True)
    os.chdir(out)
    generate_netlist(file_=f"{name}.net", do_backup=False)

    sheet = Sheet(name, title)
    layout(sheet, pico, blocks)
    sheet.write(f"{name}.kicad_sch")
    subprocess.run(["kicad-cli", "sch", "export", "pdf", "-o", f"{name}.pdf", f"{name}.kicad_sch"],
                   check=True, capture_output=True)

    def connections(netlist):
        nets = set()
        for net in children(child(parse(open(netlist).read()), "nets"), "net"):
            nodes = frozenset((str(child(n, "ref")[1]), str(child(n, "pin")[1])) for n in children(net, "node")
                              if not str(child(n, "ref")[1]).startswith("#"))
            if len(nodes) > 1:
                nets.add(nodes)
        return nets

    subprocess.run(["kicad-cli", "sch", "export", "netlist", "-o", f"{name}-drawn.net", f"{name}.kicad_sch"],
                   check=True, capture_output=True)
    wanted, drawn = connections(f"{name}.net"), connections(f"{name}-drawn.net")
    if wanted != drawn:
        for n in sorted(wanted - drawn, key=sorted):
            print("  in the circuit, not drawn:", sorted(n), file=sys.stderr)
        for n in sorted(drawn - wanted, key=sorted):
            print("  drawn, not in the circuit:", sorted(n), file=sys.stderr)
        sys.exit(f"{name}.kicad_sch does not match the circuit")

    subprocess.run(["kicad-cli", "sch", "erc", "--format", "json", "-o", f"{name}-erc.json", f"{name}.kicad_sch"],
                   check=True, capture_output=True)
    # The library-table warnings only say this KiCad has no global tables set up.
    problems = [v for sh in json.load(open(f"{name}-erc.json"))["sheets"] for v in sh["violations"]
                if v["type"] not in ("lib_symbol_issues", "footprint_link_issues")]
    for v in problems:
        print(f"  ERC {v['severity']}: {v['description']}", file=sys.stderr)
    if any(v["severity"] == "error" for v in problems):
        sys.exit(f"{name}.kicad_sch has ERC errors")
    print(f"wrote {out}/{name}.kicad_sch: {len(drawn)} nets, drawing matches the circuit, "
          f"{len(problems)} ERC warnings")
