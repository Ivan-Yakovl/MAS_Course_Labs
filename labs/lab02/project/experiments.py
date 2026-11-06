"""Reproduce all raw rows, aggregate summaries, and example traces."""
import csv
import hashlib
import json
import math
import platform
import statistics as st
import sys
from pathlib import Path
from mas import Warehouse, ResourceTrial, ReliabilityTrial, SCENARIOS, write_json

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "results"


def save_csv(name, rows):
    OUT.mkdir(exist_ok=True)
    with (OUT/name).open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v) if isinstance(v, list) else v for k,v in row.items()})


def mean_ci(values):
    n = len(values)
    mu = st.mean(values)
    # Student t critical for df=29 (all main experiments have 30 paired seeds).
    half = 2.04523*st.stdev(values)/math.sqrt(n) if n > 1 else 0
    return dict(mean=mu, ci95_half=half, n=n)


def aggregate(rows, keys, metrics):
    groups = {}
    for row in rows:
        groups.setdefault(tuple(row[k] for k in keys), []).append(row)
    return [dict(zip(keys, group), **{m:mean_ci([r[m] for r in subset]) for m in metrics})
            for group, subset in groups.items()]


def run_all():
    rows2, rows3, rows4 = [], [], []
    for robots, spacing, latency in ((6,2,0), (6,2,1), (6,2,3), (12,2,1), (6,0,1)):
        for seed in range(30):
            for mechanism in ("central", "contract"):
                sim = Warehouse(seed, n_robots=robots, spacing=spacing,
                                mechanism=mechanism, latency=latency, retain=False)
                rows2.append(dict(seed=seed, spacing=spacing, **sim.run()))
    for mixed in (False, True):
        for seed in range(30):
            for policy in ("greedy", "negotiated"):
                sim = ResourceTrial(seed, policy, mixed=mixed, retain=False)
                rows3.append(dict(seed=seed, mixed=mixed, **sim.run()))
    for scenario in SCENARIOS:
        for seed in range(30):
            for protected in (False, True):
                sim = ReliabilityTrial(seed, scenario, protected, retain=False)
                rows4.append(dict(seed=seed, **sim.run()))
    save_csv("lab2_raw.csv", rows2)
    save_csv("lab3_raw.csv", rows3)
    save_csv("lab4_raw.csv", rows4)
    summary = dict(
        lab2=aggregate(rows2, ["robots", "spacing", "latency", "mechanism"],
                       ["makespan", "flow_mean", "messages", "on_time", "distance", "bid_evals", "coord_delay"]),
        lab3=aggregate(rows3, ["mixed", "policy"],
                       ["completed", "fairness", "max_queue_age", "starved", "messages", "weighted_value", "wait_mean"]),
        lab4=aggregate(rows4, ["scenario", "protected"],
                       ["physical", "success", "safe", "ticks", "messages", "rejects"]))
    pairs = []
    for robots, spacing, latency in ((6,2,0), (6,2,1), (6,2,3), (12,2,1), (6,0,1)):
        a = [r for r in rows2 if r["robots"] == robots and r["spacing"] == spacing and r["latency"] == latency]
        pairs.append(dict(robots=robots, spacing=spacing, latency=latency,
                          makespan_delta_contract_minus_central=mean_ci([
                              next(r["makespan"] for r in a if r["seed"] == seed and r["mechanism"] == "contract")-
                              next(r["makespan"] for r in a if r["seed"] == seed and r["mechanism"] == "central")
                              for seed in range(30)])))
    summary["paired_lab2"] = pairs
    write_json(OUT/"summary.json", summary)
    demos = [("lab1_contract", Warehouse(seed=0, mechanism="contract")),
             ("lab2_central", Warehouse(seed=0, mechanism="central")),
             ("lab3_greedy", ResourceTrial(seed=0, policy="greedy")),
             ("lab3_negotiated", ResourceTrial(seed=0, policy="negotiated"))]
    demos += [(f"lab4_{s}_{'protected' if p else 'baseline'}", ReliabilityTrial(0,s,p))
              for s in SCENARIOS for p in (False,True)]
    for name, sim in demos:
        write_json(OUT/"traces"/(name+".json"), dict(result=sim.run(), trace=sim.bus.trace))
    manifest = dict(python=sys.version, platform=platform.platform(), seeds=list(range(30)),
                    runs=dict(lab2=len(rows2),lab3=len(rows3),lab4=len(rows4)),
                    source_sha256={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
                                   for name in ("mas.py", "experiments.py")})
    write_json(OUT/"manifest.json", manifest)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    run_all()
