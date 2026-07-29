# gmid-char-sky130

A gm/Id characterisation framework for SKY130 analog design: it measures the device
curves that sizing decisions should be made from, so transistor widths come out of data
rather than out of iterating W until the simulator agrees.

## Why gm/Id

Sizing by trial and error works and teaches nothing. The gm/Id method inverts the
problem: pick the inversion level first, because gm/Id is what actually sets the
trade-offs you care about —

| gm/Id | inversion | gain per amp | speed | area |
|---:|---|---|---|---|
| ~25 | weak | high | low | large |
| ~10-15 | moderate | balanced | balanced | balanced |
| ~3-5 | strong | low | high | small |

— then read the current density `Id/W` that corresponds to it and let the width fall out:

```
W = I_target / (Id/W)
```

This works because **gm/Id and Id/W are both independent of W at fixed L**. They are
properties of the inversion level, not of the device size, so one sweep characterises
every width you will ever draw at that length.

## What it measures

For each operating point across a Vgs sweep at fixed L:

| quantity | meaning |
|---|---|
| `gm/Id` | inversion level (S/A) |
| `Id/W` | current density (A/µm) — the sizing lookup |
| `gm/gds` | intrinsic gain, the ceiling on one stage |
| `ft` | gm / (2π·Cgg), the speed limit |
| `Vth`, `Vdsat` | headroom bookkeeping |

## Results, SKY130 1.8 V devices at L = 1 µm

```
python3 char/gmid_sweep.py --device nfet --L 1.0 --out data/nfet_L1.csv
python3 char/gmid_sweep.py --device pfet --L 1.0 --out data/pfet_L1.csv
```

| | nfet | pfet |
|---|---:|---:|
| Vth | 0.577 V | **1.030 V** |
| peak gm/Id (weak inversion) | 27.0 | 28.8 |
| Id/W at gm/Id = 13.6 | 2.35 µA/µm | 0.44 µA/µm |
| peak gm/gds | 205 | 468 |
| ft at gm/Id = 8 | 1.65 GHz | 0.39 GHz |

The measured peak gm/Id of ~27 S/A is the expected `1/(n·V_T)` limit with n ≈ 1.37,
which is a useful sanity check that the models and the extraction agree.

**The asymmetry in the second row is the single most consequential fact for any 1.8 V
design in this process.** The pfet threshold is 1.03 V against a 1.8 V rail, so a PMOS
device spends more than half the supply just turning on. That one number decided the
input-pair polarity, the load inversion level, and ultimately the input common-mode range
of the amplifier in the companion repo. pfets also need roughly 5x the width of an nfet
for the same current, and repay it with about 2x the intrinsic gain — so they belong
where gain matters and headroom does not.

## Using it to size a device

```python
from gmid_sweep import load, id_per_width
rows = load("data/nfet_L1.csv")
idw  = id_per_width(rows, target_gm_id=12.57)   # A/um
W    = 10e-6 / idw                               # width for 10 uA at that inversion level
```

The companion repo [`analog-pmic-sky130`](https://github.com/nilaymastaadmi/analog-pmic-sky130)
sizes every transistor in a two-stage OTA this way.

## PDK setup, and two traps in it

The SKY130 device models come from the standalone
[`skywater-pdk-libs-sky130_fd_pr`](https://github.com/google/skywater-pdk-libs-sky130_fd_pr)
repository. `models/` holds a minimal library that pulls in only `nfet_01v8` and
`pfet_01v8` for each of the five process corners, because the PDK's own corner file also
drags in 5 V, 20 V, ESD and RF devices — and one of those includes is broken in the
standalone repo.

Two failure modes cost real time here, and both fail *quietly*, which is what makes them
worth recording:

**1. The model definitions are not where the corner name suggests.** `*.corner.spice`
contains only `.param` overrides; the actual `.subckt` and its binned `.model` statements
live in `*.pm3.spice`. Including the corner file alone yields

```
could not find a valid modelname
```

— the subcircuit exists, so the netlist parses, but it has no models inside it.

**2. `critical.spice` declares a parameter whose name collides with a subcircuit.**

```spice
.param sky130_fd_pr__pfet_01v8 = 0.0
```

That is the exact name of the pfet subcircuit. ngspice resolves the name to the
parameter, so every pfet instantiation fails with `unknown subckt` while **nfet works
perfectly** — there is no equivalent line for nfet. The asymmetry is what makes it hard
to spot: the natural conclusion is that something is wrong with your pfet netlist.
`models/patched/critical_nocollide.spice` strips that one line and keeps the rest.

**3. Model bins stop at W = 100 µm.** Anything wider matches no bin and is rejected.
Real layouts finger wide devices anyway, so the sizing script in the companion repo emits
`m=` multipliers rather than one impossible transistor.

## Layout

```
char/gmid_sweep.py     the sweep: builds decks, runs ngspice, extracts and derives
data/*.csv             characterisation output
models/                minimal per-corner SKY130 1.8 V libraries
models/patched/        critical.spice with the name collision removed
```

## Requirements

`ngspice` and Python 3. No commercial tools and no licences.
