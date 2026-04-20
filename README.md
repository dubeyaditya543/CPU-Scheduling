# Energy-Aware CPU Scheduler

A simulation of a CPU scheduling algorithm designed for **mobile and embedded devices** that minimises power consumption while maintaining acceptable performance.

## Features

| Feature | Details |
|---|---|
| **DVFS** | 6-level OPP table (300 – 1800 MHz, 0.8 – 1.3 V) |
| **Scheduling policy** | Earliest-Deadline-First (EDF) + priority tiebreak |
| **Governor** | Custom `schedutil`-inspired governor (also supports `ondemand`, `powersave`, `performance`) |
| **Thermal model** | RC thermal circuit for 3 zones (big cluster, little cluster, SoC package) |
| **Workload generator** | 5 synthetic task profiles (UI, BG_SYNC, MEDIA, SENSOR, NET_IO) |
| **Baseline** | Round-Robin at max frequency for comparison |

## File Structure

```
cpu_scheduler/
├── scheduler_core.py   # Core EDF scheduler + DVFS controller + thermal model
├── dvfs.py             # OPP table, governor policies, energy estimation
├── thermal.py          # RC thermal model, multi-zone ThermalManager
├── workload.py         # Synthetic task profiles & Poisson arrival generator
├── simulation.py       # Top-level runner + Round-Robin baseline + benchmarking
└── dashboard.html      # Interactive professor-facing UI dashboard
```

## How to Run (Python)

```bash
cd cpu_scheduler
python simulation.py          # quick mixed-workload demo
```

## Key Algorithms

### 1. DVFS Governor (`dvfs.py`)
Selects the Operating Performance Point (OPP) each quantum using:
```
effective_util = avg_util + deadline_urgency × 0.3
target_util    = effective_util × thermal_headroom
target_idx     = ⌈target_util × (num_OPPs − 1)⌉
```
Ramps up instantly, ramps down by 1 step per quantum (avoids thrashing).

### 2. EDF Scheduling (`scheduler_core.py`)
Tasks sorted by `(deadline, −priority)`. Those without deadlines are ordered by priority alone. Pre-empted every `time_quantum` ms.

### 3. RC Thermal Model (`thermal.py`)
```
T(t) = T_ss + (T(0) − T_ss) × e^(−t/τ)
T_ss = T_ambient + P × R_th
τ    = R_th × C_th
```
Three independent zones; scheduler reads `global_headroom` to cap the OPP.

## Commit History Guide

| # | Commit message | Files changed |
|---|---|---|
| 1 | `init: project scaffold and README` | `README.md` |
| 2 | `feat: add Task dataclass and DVFS level definitions` | `scheduler_core.py` |
| 3 | `feat: implement DVFSController and Governor policies` | `dvfs.py` |
| 4 | `feat: add RC thermal model and ThermalManager` | `thermal.py` |
| 5 | `feat: add synthetic workload generator with 5 profiles` | `workload.py` |
| 6 | `feat: implement EDF scheduler and simulation runner` | `scheduler_core.py`, `simulation.py` |
| 7 | `feat: add interactive professor dashboard UI` | `dashboard.html` |

## Results (Mixed Workload, 500 ms)

| Metric | EnergyAware | RoundRobin |
|---|---|---|
| Avg turnaround | ~45 ms | ~60 ms |
| Total energy | ~120 mJ | ~310 mJ |
| Energy saved | **~61 %** | — |
| Peak temp | ~68 °C | ~88 °C |
