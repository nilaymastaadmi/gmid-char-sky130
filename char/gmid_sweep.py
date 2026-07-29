#!/usr/bin/env python3
"""gm/Id characterisation sweep for SKY130 1.8V devices.

Sizing an analog stage by picking W/L until the simulator produces the number you
wanted is guesswork with extra steps. The gm/Id methodology inverts that: choose an
inversion level first (gm/Id sets the gain-per-current and the noise/speed trade-off),
read the corresponding current density Id/W off a characterisation curve, and the width
falls out as W = Id_target / (Id/W).

That works because gm/Id and Id/W are both essentially independent of W at fixed L --
they are properties of the inversion level, not the device size. This script measures
both, so every transistor in the OTA is sized from data rather than from a guess.

Sweeps Vgs at fixed Vds with the device in saturation and records, per operating point:

    Id, gm, gds, gm/Id, Id/W, gm/gds (intrinsic gain), ft = gm / (2*pi*Cgg)

Usage:
    python3 gmid_sweep.py --device nfet --L 1.0 --out nfet_L1.csv
"""

import argparse
import csv
import math
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
MODELS = HERE.parent / "models"

DEVICES = {
    "nfet": "sky130_fd_pr__nfet_01v8",
    "pfet": "sky130_fd_pr__pfet_01v8",
}


def build_netlist(device, W, L, vds, vgs_max, step, corner, temp):
    """One device, source and bulk at the local reference, swept on Vgs.

    The pfet is described in its own source-referenced frame: the netlist drives
    |Vgs| and |Vds| and flips the polarity when it instantiates the sources, so the
    swept variable means the same thing for both device types and the extracted
    curves can be compared directly.
    """
    model = DEVICES[device]
    inst = f"m.xm1.m{model}"

    if device == "nfet":
        # source/bulk at 0, drain and gate positive
        src = f"""
XM1 d g 0 0 {model} W={W}u L={L}u
Vd d 0 {vds}
Vg g 0 0
"""
        id_expr = "-i(vd)"
    else:
        # source/bulk at VDD, drain and gate below it by the swept amount
        src = f"""
Vdd vdd 0 1.8
XM1 d g vdd vdd {model} W={W}u L={L}u
Vd vdd d {vds}
Vg vdd g 0
"""
        # Drain current leaves the pfet drain and returns to vdd through Vd, i.e.
        # it flows from the source's - node to its + node, so i(vd) is negative.
        # Negating it makes |Id| positive and directly comparable to the nfet sweep.
        id_expr = "-i(vd)"

    return f"""* gm/Id sweep: {device} W={W}u L={L}u corner={corner} T={temp}C
.include "{MODELS}/sky130_18v_{corner}.spice"
{src}
.control
save all @{inst}[gm] @{inst}[gds] @{inst}[cgg] @{inst}[vth] @{inst}[vdsat]
option temp={temp}
dc Vg 0 {vgs_max} {step}
let id  = {id_expr}
let gm  = @{inst}[gm]
let gds = @{inst}[gds]
let cgg = @{inst}[cgg]
let vth = @{inst}[vth]
wrdata $outfile id gm gds cgg vth
.endc
.end
"""


def run(device, W, L, vds, vgs_max, step, corner, temp):
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        outfile = td / "raw.txt"
        deck = build_netlist(device, W, L, vds, vgs_max, step, corner, temp)
        deck = deck.replace("$outfile", str(outfile))
        netlist = td / "sweep.spice"
        netlist.write_text(deck)

        proc = subprocess.run(["ngspice", "-b", str(netlist)],
                              capture_output=True, text=True, timeout=900)
        if not outfile.exists():
            sys.stderr.write(proc.stdout[-3000:] + "\n" + proc.stderr[-2000:] + "\n")
            raise RuntimeError("ngspice produced no output -- see log above")

        rows = []
        for line in outfile.read_text().splitlines():
            parts = line.split()
            if len(parts) < 10:
                continue
            try:
                vals = [float(x) for x in parts]
            except ValueError:
                continue
            # wrdata emits an x column before every y column
            vgs, idd = vals[0], vals[1]
            gm, gds, cgg, vth = vals[3], vals[5], vals[7], vals[9]
            rows.append(dict(vgs=vgs, id=idd, gm=gm, gds=gds, cgg=cgg, vth=vth))
        return rows


def enrich(rows, W):
    """Add the derived quantities that sizing actually uses."""
    out = []
    for r in rows:
        idd, gm, gds, cgg = r["id"], r["gm"], r["gds"], r["cgg"]
        if idd <= 0 or gm <= 0:
            continue
        r = dict(r)
        r["gm_id"] = gm / idd                       # inversion level, S/A
        r["id_w"] = idd / W                         # current density, A per um
        r["gain"] = gm / gds if gds > 0 else float("nan")   # intrinsic gain gm/gds
        r["ft"] = gm / (2 * math.pi * cgg) if cgg > 0 else float("nan")
        out.append(r)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", choices=DEVICES, default="nfet")
    ap.add_argument("--W", type=float, default=10.0, help="width in um (curves are W-independent)")
    ap.add_argument("--L", type=float, default=1.0, help="length in um")
    ap.add_argument("--vds", type=float, default=0.9, help="|Vds| in V, keep in saturation")
    ap.add_argument("--vgs-max", type=float, default=1.8)
    ap.add_argument("--step", type=float, default=0.01)
    ap.add_argument("--corner", default="tt", choices=["tt", "ff", "ss", "sf", "fs"])
    ap.add_argument("--temp", type=float, default=27)
    ap.add_argument("--out", default=None, help="CSV output path")
    args = ap.parse_args()

    rows = enrich(run(args.device, args.W, args.L, args.vds,
                      args.vgs_max, args.step, args.corner, args.temp), args.W)
    if not rows:
        sys.exit("no valid operating points extracted")

    if args.out:
        with open(args.out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"wrote {len(rows)} points -> {args.out}")

    print(f"\n{args.device} W={args.W}u L={args.L}u {args.corner} {args.temp}C")
    print(f"{'Vgs':>6} {'Id(uA)':>9} {'gm(uS)':>9} {'gm/Id':>7} "
          f"{'Id/W(uA/um)':>12} {'gm/gds':>8} {'ft(GHz)':>9}")
    for r in rows:
        if round(r["vgs"] * 100) % 10:      # print every 0.1 V
            continue
        print(f"{r['vgs']:6.2f} {r['id']*1e6:9.3f} {r['gm']*1e6:9.2f} "
              f"{r['gm_id']:7.2f} {r['id_w']*1e6:12.4f} {r['gain']:8.1f} "
              f"{r['ft']/1e9:9.3f}")


if __name__ == "__main__":
    main()
