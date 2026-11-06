"""Independent repeat of every stored run and trace-based checks.

Python standard library only. This audit does not reuse the aggregation helpers
in experiments.py. It verifies outputs, counts actual service intervals and
recomputes all reported means and confidence intervals from raw CSV files.
"""
import csv
import hashlib
import json
import math
import statistics
import unittest
from collections import Counter
from pathlib import Path
from mas import Warehouse, ResourceTrial, ReliabilityTrial, write_json

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"


def read_rows(lab):
    with (RESULTS/f"lab{lab}_raw.csv").open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def match(value, stored, context):
    if isinstance(value, list):
        assert value == json.loads(stored), context
    elif isinstance(value, bool):
        assert str(value) == stored, context
    elif isinstance(value, (int, float)):
        assert math.isclose(value, float(stored), rel_tol=1e-12, abs_tol=1e-12), context
    elif value is None:
        assert stored == "", context
    else:
        assert value == stored, context


def check_stats(records, metric, target):
    values = [float(r[metric]) for r in records]
    mean = sum(values)/len(values)
    # Independent centered sum of squares rather than experiments.mean_ci.
    variance = sum((x-mean)**2 for x in values)/(len(values)-1)
    half = 2.04523*math.sqrt(variance/len(values))
    assert len(values) == target["n"] == 30
    assert math.isclose(mean, target["mean"], rel_tol=1e-12, abs_tol=1e-12)
    assert math.isclose(half, target["ci95_half"], rel_tol=1e-10, abs_tol=1e-12)


def main():
    counts = Counter()
    all_rows = {lab:read_rows(lab) for lab in (2,3,4)}
    for lab, rows in all_rows.items():
        for row in rows:
            seed = int(row["seed"])
            if lab == 2:
                sim = Warehouse(seed=seed, n_robots=int(row["robots"]), spacing=int(row["spacing"]),
                                mechanism=row["mechanism"], latency=int(row["latency"]))
            elif lab == 3:
                sim = ResourceTrial(seed=seed, policy=row["policy"], mixed=row["mixed"] == "True")
            else:
                sim = ReliabilityTrial(seed=seed, scenario=row["scenario"], protected=row["protected"] == "True")
            actual = sim.run()
            for key, value in actual.items():
                match(value,row[key],(lab,seed,key))
                counts["matched_metrics"] += 1
            events = sim.bus.trace
            assert [e["seq"] for e in events] == list(range(1,len(events)+1))
            assert len(events) == actual["messages"]
            grants = sorted((e["tick"],e["end"]) for e in events if e["kind"] == "GRANT")
            assert all(a[1] <= b[0] for a,b in zip(grants,grants[1:])), (lab,seed,"capacity")
            if lab == 2:
                done = [e["task"] for e in events if e["kind"] == "DONE"]
                assert len(done) == len(set(done)) == 120
                m = int(row["robots"])//3
                expected = 120*(6 if row["mechanism"] == "central" else 3*m+5)
                assert len(events) == expected
            elif lab == 3:
                served = Counter(e["dst"] for e in events if e["kind"] == "GRANT" and e["end"] <= sim.horizon)
                values = [served[i] for i in range(sim.n)]
                assert values == actual["served"]
                assert sum(values) == actual["completed"]
                fairness = sum(values)**2/(sim.n*sum(v*v for v in values))
                assert math.isclose(fairness,actual["fairness"])
            else:
                physical = {e["task"] for e in events if e["kind"] == "RELEASE"}
                assert len(physical) == actual["physical"]
                if row["protected"] == "True":
                    assert actual["success"] == 1
                    assert actual["accounting"] == actual["accepted"] == actual["physical"] == 12
                    if row["scenario"] in ("clean", "tool_outage"):
                        assert actual["rejects"] == 0
            counts["reproduced_runs"] += 1
            counts["capacity_checked_traces"] += 1
    summary = json.loads((RESULTS/"summary.json").read_text(encoding="utf-8"))
    keys = {2:("robots","spacing","latency","mechanism"),
            3:("mixed","policy"),4:("scenario","protected")}
    for lab in (2,3,4):
        for group in summary[f"lab{lab}"]:
            records = [r for r in all_rows[lab] if all(r[k] == str(group[k]) for k in keys[lab])]
            for metric,target in group.items():
                if isinstance(target,dict):
                    check_stats(records,metric,target)
                    counts["checked_statistical_summaries"] += 1
    for group in summary["paired_lab2"]:
        subset = [r for r in all_rows[2] if all(r[k] == str(group[k]) for k in ("robots","spacing","latency"))]
        differences = []
        for seed in range(30):
            pair = {r["mechanism"]:float(r["makespan"]) for r in subset if int(r["seed"]) == seed}
            differences.append({"delta":pair["contract"]-pair["central"]})
        check_stats(differences,"delta",group["makespan_delta_contract_minus_central"])
        counts["checked_statistical_summaries"] += 1
    suite = unittest.defaultTestLoader.discover(str(ROOT), pattern="test_mas.py")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    assert result.wasSuccessful()
    audit = dict(status="passed", **counts, unit_tests=result.testsRun,
                 raw_file_sha256={f"lab{lab}_raw.csv":hashlib.sha256((RESULTS/f"lab{lab}_raw.csv").read_bytes()).hexdigest()
                                  for lab in (2,3,4)},
                 source_sha256={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
                                for name in ("mas.py","experiments.py","test_mas.py","audit_results.py")})
    write_json(RESULTS/"quality_audit.json",audit)
    print(json.dumps(audit,indent=2))


if __name__ == "__main__":
    main()
