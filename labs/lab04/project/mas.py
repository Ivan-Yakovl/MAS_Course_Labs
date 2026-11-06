"""Explicit warehouse MAS. Python 3.10+, standard library only.

No framework implements allocation, arbitration, or protection policies.
Time is an integer simulation tick; agents are logical state machines in one process.
"""
from __future__ import annotations
import argparse
import json
import math
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Task:
    id: int
    arrival: int
    skill: int
    position: int
    work: int
    deadline: int
    priority: int


@dataclass
class Robot:
    id: int
    skill: int
    speed: float
    position: int
    task: int | None = None
    ready: int = 0
    served: int = 0

    def bid(self, task: Task, now: int, latency: int) -> int:
        """Private local estimate; busy agents refuse instead of queueing work."""
        if self.task is not None or task.skill != self.skill:
            raise ValueError("Agent cannot bid")
        return now + 4 * latency + math.ceil(
            (abs(self.position - task.position) + task.work) / self.speed) + 2


class Bus:
    def __init__(self, retain=True):
        self.trace = []
        self.count = 0
        self.types = Counter()
        self.retain = retain

    def send(self, t, src, dst, kind, task=None, **payload):
        self.count += 1
        self.types[kind] += 1
        event = dict(seq=self.count, tick=t, src=src, dst=dst,
                     kind=kind, task=task, **payload)
        if self.retain:
            self.trace.append(event)
        return event


def workload(seed=0, n_tasks=120, n_robots=6, spacing=2):
    rng = random.Random(seed)
    robots = [Robot(i, i % 3, rng.choice([0.8, 1.0, 1.6]),
                    rng.randrange(31)) for i in range(n_robots)]
    tasks = []
    now = 0
    for j in range(n_tasks):
        now += rng.randrange(spacing + 1)
        tasks.append(Task(j, now, rng.randrange(3), rng.randrange(31),
                          rng.randint(4, 12), now + rng.randint(45, 100),
                          rng.randint(1, 3)))
    return tasks, robots


class Warehouse:
    """Order allocation and a single capacity-one loading station."""
    def __init__(self, seed=0, n_tasks=120, n_robots=6, spacing=2,
                 mechanism="central", latency=1, retain=True):
        if mechanism not in ("central", "contract"):
            raise ValueError(mechanism)
        self.tasks, self.robots = workload(seed, n_tasks, n_robots, spacing)
        self.mechanism, self.latency = mechanism, latency
        self.bus = Bus(retain)
        self.status = {q.id: "new" for q in self.tasks}
        self.waiting = {}  # task -> (robot, ready tick)
        self.station = None  # (robot, task, end tick)
        self.completed = {}
        self.cursor = 0
        self.distance = 0
        self.bid_evals = 0
        self.coordination_delay = 0
        self.violations = 0

    def allocate(self, q, t):
        eligible = [r for r in self.robots if r.skill == q.skill]
        idle = [r for r in eligible if r.task is None]
        if not idle:
            return
        if self.mechanism == "central":
            # Cached static capabilities and completion reports; cyclic selection.
            ordered = sorted(idle, key=lambda r: ((r.id-self.cursor) % len(self.robots)))
            winner = ordered[0]
            self.cursor = (winner.id + 1) % len(self.robots)
            self.bus.send(t, "D", winner.id, "ASSIGN", q.id)
            delay = 2 * self.latency
        else:
            bids = []
            for r in eligible:
                self.bus.send(t, "D", r.id, "CFP", q.id)
                if r.task is None:
                    bid = r.bid(q, t, self.latency)
                    self.bid_evals += 1
                    self.bus.send(t+self.latency, r.id, "D", "PROPOSE", q.id, finish=bid)
                    bids.append((bid, r.id, r))
                else:
                    self.bus.send(t+self.latency, r.id, "D", "REFUSE", q.id)
            winner = min(bids, key=lambda b: b[:2])[2]
            for r in eligible:
                self.bus.send(t+2*self.latency, "D", r.id,
                              "ACCEPT" if r.id == winner.id else "REJECT", q.id)
            delay = 4 * self.latency
        self.bus.send(t+delay, winner.id, "D", "ACK", q.id)
        self.coordination_delay += delay
        travel = abs(winner.position-q.position)
        self.distance += travel
        winner.ready = t + delay + math.ceil((travel+q.work)/winner.speed)
        winner.task = q.id
        self.status[q.id] = "working"

    def run(self, horizon=20000):
        last_tick = 0
        for t in range(horizon+1):
            last_tick = t
            # Completion before requests; a station slot spans [start, end).
            if self.station and self.station[2] <= t:
                rid, jid, _ = self.station
                r = self.robots[rid]
                self.status[jid] = "done"
                self.completed[jid] = t
                r.task, r.position = None, self.tasks[jid].position
                r.served += 1
                self.bus.send(t, rid, "D", "DONE", jid)
                self.bus.send(t, rid, "S", "RELEASE", jid)
                self.station = None
            for r in self.robots:
                if r.task is not None and self.status[r.task] == "working" and r.ready <= t:
                    self.status[r.task] = "waiting"
                    self.waiting[r.task] = (r.id, t)
                    self.bus.send(t, r.id, "S", "REQUEST", r.task)
            if self.station is None and self.waiting:
                jid = min(self.waiting, key=lambda j: (self.waiting[j][1], self.waiting[j][0]))
                rid, _ = self.waiting.pop(jid)
                self.station = (rid, jid, t+2)
                self.status[jid] = "service"
                self.bus.send(t, "S", rid, "GRANT", jid, end=t+2)
            for q in self.tasks:
                if self.status[q.id] == "new" and q.arrival <= t:
                    self.allocate(q, t)
            # Invariant is evaluated from actual state, not from counters.
            active = [r.task for r in self.robots if r.task is not None]
            if len(active) != len(set(active)):
                self.violations += 1
            if any(r.task is not None and self.tasks[r.task].skill != r.skill for r in self.robots):
                self.violations += 1
            if len(self.completed) == len(self.tasks):
                break
        times = [self.completed[j]-self.tasks[j].arrival for j in self.completed]
        late = [max(0, end-self.tasks[j].deadline) for j, end in self.completed.items()]
        return dict(mechanism=self.mechanism, tasks=len(self.tasks), robots=len(self.robots),
                    latency=self.latency, completed=len(times), makespan=last_tick,
                    flow_mean=sum(times)/len(times) if times else 0,
                    lateness_mean=sum(late)/len(late) if late else 0,
                    on_time=sum(self.completed[j] <= self.tasks[j].deadline for j in self.completed)/len(self.tasks),
                    messages=self.bus.count, messages_per_task=self.bus.count/len(self.tasks),
                    bid_evals=self.bid_evals, distance=self.distance,
                    coord_delay=self.coordination_delay, violations=self.violations)


def jain(values):
    denom = len(values)*sum(x*x for x in values)
    return sum(values)**2/denom if denom else 0.0


class ResourceTrial:
    """Same station and worker roles, isolated saturated-resource experiment.

    Each worker has a private endless backlog, approximated by horizon+1 jobs.
    Equal demand makes service-count fairness interpretable. Service durations
    and priorities are pre-generated per (worker, request), shared across modes.
    """
    def __init__(self, seed=0, policy="greedy", horizon=240, n=6,
                 mixed=False, retain=True):
        if policy not in ("greedy", "negotiated"):
            raise ValueError(policy)
        self.seed, self.policy, self.horizon, self.n = seed, policy, horizon, n
        self.bus = Bus(retain)
        rng = random.Random(seed)
        self.durations = [[rng.randint(2, 4) for _ in range(horizon+1)] for _ in range(n)]
        self.priorities = [[rng.randint(1, 3) if mixed else 1
                            for _ in range(horizon+1)] for _ in range(n)]
        self.served = [0]*n
        self.wait_since = [0]*n
        self.wait_samples = []
        self.weighted = 0
        self.active = None
        self.max_queue_age = 0
        self.violations = 0

    def run(self):
        for i in range(self.n):
            self.bus.send(0, i, "S", "REQUEST", 0)
        for t in range(self.horizon+1):
            if self.active and self.active[1] == t:
                i, _ = self.active
                self.weighted += self.priorities[i][self.served[i]]
                self.served[i] += 1
                self.active = None
                self.wait_since[i] = t
                self.bus.send(t, i, "S", "RELEASE", self.served[i]-1)
                self.bus.send(t, i, "S", "REQUEST", self.served[i])
            waiting = [i for i in range(self.n) if not self.active or i != self.active[0]]
            self.max_queue_age = max(self.max_queue_age,
                                     max((t-self.wait_since[i] for i in waiting), default=0))
            if t == self.horizon or self.active:
                continue
            if self.policy == "greedy":
                winner = min(waiting)
            else:
                # PROPOSE -> local offer -> arbitration -> ACCEPT/DEFER.
                # Aging is unbounded; priority and service credit are bounded.
                scores = {}
                for i in waiting:
                    age = t-self.wait_since[i]
                    priority = self.priorities[i][self.served[i]]
                    score = age + 4*priority + 8/(1+self.served[i])
                    self.bus.send(t, "S", i, "PROPOSE_SLOT", self.served[i])
                    self.bus.send(t, i, "S", "OFFER", self.served[i],
                                  age=age, priority=priority, score=score)
                    scores[i] = score
                winner = max(waiting, key=lambda i: (scores[i], -i))
                for i in waiting:
                    self.bus.send(t, "S", i, "ACCEPT_SLOT" if i == winner else "DEFER",
                                  self.served[i], winner=winner)
            self.wait_samples.append(t-self.wait_since[winner])
            self.active = (winner, t+self.durations[winner][self.served[winner]])
            self.bus.send(t, "S", winner, "GRANT", self.served[winner], end=self.active[1])
        total = sum(self.served)
        return dict(policy=self.policy, completed=total, throughput=total/self.horizon,
                    fairness=jain(self.served), min_service=min(self.served),
                    max_service=max(self.served), starved=sum(x == 0 for x in self.served),
                    max_queue_age=self.max_queue_age,
                    wait_mean=sum(self.wait_samples)/len(self.wait_samples),
                    weighted_value=self.weighted, messages=self.bus.count,
                    violations=self.violations, served=self.served)


SCENARIOS = ("clean", "false_done", "goal_hijack", "tool_outage",
             "forged_assignment", "delegation_cycle", "stale_cancel", "replay_done")


class ReliabilityTrial:
    """Fault injection into an explicit order/control plane of the same MAS.

    Two identical worker agents execute twelve FIFO jobs. Immutable physical
    evidence is owned by the environment; control messages cannot forge it.
    The station has capacity one. Baseline trusts authenticated-looking claims;
    protected mode checks sender roles, versions, leases and physical evidence.
    This small configuration isolates failure causes from allocation performance.
    """
    def __init__(self, seed=0, scenario="clean", protected=False, retain=True):
        if scenario not in SCENARIOS:
            raise ValueError(scenario)
        self.seed, self.scenario, self.protected = seed, scenario, protected
        self.bus = Bus(retain)
        rng = random.Random(seed)
        self.costs = [rng.randint(5, 9) for _ in range(12)]
        self.state = {j: "new" for j in range(12)}
        self.owner = {}
        self.ready = {}
        self.version = {j: 1 for j in range(12)}
        self.lease = {}
        self.physical = set()
        self.accepted = set()
        self.credits = 0
        self.quarantine = set()
        self.station = None
        self.tool_failed = False
        self.retry_at = 0
        self.rejects = 0
        self.violations = set()
        self.cycles = []
        self.injected = False
        self.attack_tick = None
        self.attack_job = 0
        self.detect_tick = None
        self.recover_tick = None

    def reject(self, t, message, why):
        self.rejects += 1
        if self.detect_tick is None:
            self.detect_tick = t
        self.bus.send(t, "M", message["src"], "REJECT_INVALID", message.get("task"), reason=why)

    def receive(self, t, src, kind, j, **p):
        msg = self.bus.send(t, src, "D", kind, j, **p)
        if self.protected:
            required = {"DONE": ("version", "lease"),
                        "ASSIGN_OVERRIDE": ("version", "new_owner"),
                        "DELEGATE": ("target", "path"), "CANCEL": ("version",)}
            if j not in self.state:
                self.reject(t, msg, "unknown_task")
                return
            if kind not in required or any(key not in p for key in required[kind]):
                self.reject(t, msg, "invalid_message_schema")
                return
        if kind == "DONE":
            if self.protected:
                if j in self.accepted:
                    self.reject(t, msg, "duplicate_completion")
                    return
                if (src != self.owner.get(j) or p.get("version") != self.version[j]
                        or p.get("lease") != self.lease.get(j) or j not in self.physical
                        or t > self.lease.get(j, -1)):
                    self.reject(t, msg, "invalid_completion_evidence")
                    return
            if j not in self.physical:
                self.violations.add("completion_without_physical_evidence")
            if j in self.accepted:
                self.violations.add("double_accounting")
            self.accepted.add(j)
            self.credits += 1
            self.state[j] = "done"
        elif kind == "ASSIGN_OVERRIDE":
            if self.protected and (src != "D" or p.get("version") != self.version[j]):
                self.reject(t, msg, "unauthorized_assignment")
                return
            if src != "D":
                self.violations.add("unauthorized_assignment")
            self.owner[j] = p["new_owner"]
        elif kind == "DELEGATE":
            path = p.get("path", [])
            target = p["target"]
            if self.protected:
                # Worker ownership is non-delegable. Dispatcher may reassign
                # only after revoking the old lease. Also bound hops / cycles.
                self.reject(t, msg, "worker_cannot_delegate" if src != "D"
                            else "cycle_or_hop_limit" if target in path or len(path) >= 3
                            else "missing_revocation")
                return
            if target in path:
                self.violations.add("cyclic_delegation")
            self.owner[j] = target
            self.state[j] = "delegating"
            self.cycles.append((t+1, target, j, path+[target]))
        elif kind == "CANCEL":
            if self.protected and (src != "D" or p.get("version", 0) <= self.version[j]):
                self.reject(t, msg, "unauthorized_or_stale_cancel")
                return
            if src != "D" or p.get("version", 0) <= self.version[j]:
                self.violations.add("stale_or_unauthorized_cancel")
            self.version[j] = p.get("version", self.version[j])
            self.state[j] = "cancelled"

    def inject(self, t):
        injected_messages = ("false_done", "goal_hijack", "forged_assignment",
                             "delegation_cycle", "stale_cancel")
        if self.scenario not in injected_messages or self.injected or not self.owner:
            return
        j = 0
        if t != 2:
            return
        self.injected, self.attack_tick = True, t
        src = self.owner[j]
        if self.scenario == "false_done":
            self.receive(t, src, "DONE", j, version=1, lease=self.lease[j])
        elif self.scenario == "goal_hijack":
            self.ready[j] = 10**6
            self.bus.send(t, src, "M", "LOCAL_GOAL_CHANGED", j, goal="hold_job_forever")
        elif self.scenario == "forged_assignment":
            self.receive(t, "W1", "ASSIGN_OVERRIDE", j, new_owner="intruder", version=1)
        elif self.scenario == "delegation_cycle":
            self.receive(t, src, "DELEGATE", j, target="W1", path=[src])
        elif self.scenario == "stale_cancel":
            self.receive(t, "W1", "CANCEL", j, version=0)
        # Outage is a physical interval [10,45); replay is triggered on DONE.

    def station_available(self, t):
        """Environmental input; recovery policy must not inspect scenario labels."""
        return not (self.scenario == "tool_outage" and 10 <= t < 45)

    def renew_lease(self, t, j):
        self.lease[j] = t + 20
        self.bus.send(t, "M", "D", "LEASE_EXTEND", j, lease=self.lease[j])
        self.bus.send(t, "D", self.owner[j], "LEASE_RENEWED", j, lease=self.lease[j])

    def run(self, horizon=180):
        final_t = 0
        for t in range(horizon+1):
            final_t = t
            outage = not self.station_available(t)
            if outage and self.attack_tick is None:
                self.attack_tick, self.attack_job = t, None
            self.inject(t)
            pending_cycles, self.cycles = self.cycles, []
            for at, sender, j, path in pending_cycles:
                if at <= t:
                    target = "W0" if sender == "W1" else "W1"
                    self.receive(t, sender, "DELEGATE", j, target=target, path=path)
                else:
                    self.cycles.append((at, sender, j, path))
            if self.station and self.station[2] == t:
                worker, j, _ = self.station
                self.physical.add(j)
                self.receive(t, worker, "DONE", j, version=self.version[j], lease=self.lease[j])
                if (self.protected and self.attack_job is None and self.attack_tick is not None
                        and not outage and self.recover_tick is None):
                    self.recover_tick = t
                self.bus.send(t, worker, "S", "RELEASE", j)
                self.station = None
                if self.scenario == "replay_done" and len(self.physical) == 1:
                    self.attack_tick, self.attack_job = t, j
                    self.receive(t, worker, "DONE", j, version=self.version[j], lease=self.lease[j])
            if self.protected:
                # Lease timeout is 20 ticks, > max work (9) + service (2)
                # + one competing service (2). Worker is quarantined.
                for j in list(self.owner):
                    if self.state[j] in ("working", "waiting") and t > self.lease[j]:
                        worker = self.owner[j]
                        # Verified waiting for the shared resource is not a
                        # worker failure, including its recovery/queue-drain phase.
                        if self.state[j] == "waiting":
                            self.renew_lease(t, j)
                            continue
                        self.quarantine.add(worker)
                        self.version[j] += 1
                        self.state[j] = "new"
                        self.owner.pop(j)
                        self.reject(t, {"src": worker, "task": j}, "lease_timeout")
            for worker in ("W0", "W1"):
                if worker in self.quarantine:
                    continue
                if any(self.owner.get(j) == worker and self.state[j] in
                       ("working", "waiting", "service", "delegating") for j in self.state):
                    continue
                free = [j for j in self.state if self.state[j] == "new"]
                if free:
                    j = min(free)
                    self.owner[j] = worker
                    self.state[j] = "working"
                    self.ready[j] = t+self.costs[j]
                    self.lease[j] = t+20
                    self.bus.send(t, "D", worker, "ASSIGN", j, version=self.version[j], lease=self.lease[j])
                    self.bus.send(t, worker, "D", "ACK", j)
            for j in self.state:
                if self.state[j] == "working" and self.ready[j] <= t:
                    self.state[j] = "waiting"
                    self.bus.send(t, self.owner[j], "S", "REQUEST", j)
            waiting = [j for j in self.state if self.state[j] == "waiting"]
            if not self.station and waiting and (not self.tool_failed or
                                                  self.protected and t >= self.retry_at):
                if outage:
                    self.tool_failed = True
                    self.retry_at = t+5
                    self.bus.send(t, "S", "M", "TOOL_UNAVAILABLE", min(waiting))
                    if self.protected and self.detect_tick is None:
                        self.detect_tick = t
                else:
                    if self.tool_failed:
                        self.bus.send(t, "M", "S", "TOOL_RECOVERED", min(waiting))
                    self.tool_failed = False
                    j = min(waiting)
                    worker = self.owner[j]
                    # The environment admits only registered workers.
                    if worker not in ("W0", "W1"):
                        continue
                    # A valid resumed slot must finish within its authorization.
                    if self.protected and self.lease[j] < t+2:
                        self.renew_lease(t, j)
                    self.state[j] = "service"
                    self.station = (worker, j, t+2)
                    self.bus.send(t, "S", worker, "GRANT", j, end=t+2)
            if self.protected and self.attack_tick is not None and self.recover_tick is None:
                if (self.attack_job in self.physical and self.attack_job in self.accepted
                        and t >= self.attack_tick):
                    self.recover_tick = t
            if len(self.accepted) == 12:
                break
        physical = len(self.physical)
        consistent = self.accepted == self.physical and self.credits == len(self.accepted)
        safe = not self.violations and consistent
        success = physical == 12 and len(self.accepted) == 12 and safe
        return dict(scenario=self.scenario, protected=self.protected, physical=physical,
                    accepted=len(self.accepted), accounting=self.credits, safe=int(safe),
                    success=int(success), ticks=final_t, messages=self.bus.count,
                    rejects=self.rejects, violations=sorted(self.violations),
                    detection_delay=None if self.detect_tick is None or self.attack_tick is None
                    else max(0, self.detect_tick-self.attack_tick),
                    recovery_delay=None if self.recover_tick is None or self.attack_tick is None
                    else max(0, self.recover_tick-self.attack_tick))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Warehouse MAS demonstration")
    ap.add_argument("--lab", type=int, choices=(1, 2, 3, 4), default=2)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--mechanism", choices=("central", "contract"), default="contract")
    ap.add_argument("--policy", choices=("greedy", "negotiated"), default="negotiated")
    ap.add_argument("--scenario", choices=SCENARIOS, default="false_done")
    ap.add_argument("--protected", action="store_true")
    ap.add_argument("--out", default="demo_trace.json")
    args = ap.parse_args()
    if args.lab in (1, 2):
        sim = Warehouse(seed=args.seed, mechanism=args.mechanism)
    elif args.lab == 3:
        sim = ResourceTrial(seed=args.seed, policy=args.policy)
    else:
        sim = ReliabilityTrial(seed=args.seed, scenario=args.scenario, protected=args.protected)
    result = sim.run()
    write_json(args.out, dict(result=result, trace=sim.bus.trace))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("Trace:", Path(args.out).resolve())


if __name__ == "__main__":
    main()
