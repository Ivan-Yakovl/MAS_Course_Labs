"""Behavioral checks: exact-once, physical safety, protocol differences, recovery."""
import unittest
from mas import Warehouse, ResourceTrial, ReliabilityTrial, SCENARIOS, jain


class MASChecks(unittest.TestCase):
    def test_allocation_completion_and_repeatability(self):
        for seed in (0, 7, 23):
            for mode in ("central", "contract"):
                a = Warehouse(seed=seed, mechanism=mode)
                r = a.run()
                b = Warehouse(seed=seed, mechanism=mode)
                self.assertEqual(r, b.run())
                self.assertEqual(a.bus.trace, b.bus.trace)
                self.assertEqual(r["completed"], 120)
                self.assertEqual(r["violations"], 0)
                self.assertEqual(len(a.bus.trace), r["messages"])
                self.assertEqual(sum(e["kind"] == "DONE" for e in a.bus.trace), 120)

    def test_contract_is_real_protocol(self):
        a = Warehouse(seed=4, mechanism="central")
        b = Warehouse(seed=4, mechanism="contract")
        ra, rb = a.run(), b.run()
        self.assertNotIn("CFP", a.bus.types)
        self.assertEqual(b.bus.types["ACCEPT"], 120)
        self.assertGreater(b.bus.types["PROPOSE"], 0)
        self.assertGreater(rb["messages"], ra["messages"])

    def test_resource_tradeoff(self):
        for seed in (0,7,23):
            a = ResourceTrial(seed, "greedy").run()
            b = ResourceTrial(seed, "negotiated").run()
            self.assertEqual(a["starved"], 5)
            self.assertEqual(b["starved"], 0)
            self.assertGreater(b["fairness"], .98)
            self.assertGreater(b["messages"], a["messages"])

    def test_clean_and_faults(self):
        for seed in range(30):
            for s in SCENARIOS:
                a = ReliabilityTrial(seed,s,False).run()
                b = ReliabilityTrial(seed,s,True).run()
                self.assertEqual(b["success"], 1, (seed,s,b))
                self.assertEqual(b["physical"], 12)
                self.assertEqual(b["accounting"], 12)
                if s in ("clean", "tool_outage"):
                    self.assertEqual(b["rejects"], 0, (seed, s, b))
                if s == "clean":
                    self.assertEqual(a["success"], 1)
                else:
                    self.assertEqual(a["success"], 0, (seed,s,a))

    def test_index_edges(self):
        self.assertEqual(jain([1,1,1]), 1)
        self.assertAlmostEqual(jain([3,0,0]), 1/3)
        self.assertEqual(jain([0,0,0]), 0)

    def test_station_capacity_from_independent_trace(self):
        for sim in (Warehouse(), ResourceTrial(policy="negotiated"),
                    ReliabilityTrial(scenario="goal_hijack", protected=True)):
            sim.run()
            grants = [e for e in sim.bus.trace if e["kind"] == "GRANT"]
            intervals = sorted((e["tick"],e["end"]) for e in grants)
            for previous, current in zip(intervals, intervals[1:]):
                self.assertLessEqual(previous[1], current[0])

    def test_equal_inputs_across_mechanisms(self):
        a, b = Warehouse(seed=13,mechanism="central"), Warehouse(seed=13,mechanism="contract")
        self.assertEqual(a.tasks,b.tasks)
        self.assertEqual(a.robots,b.robots)

    def test_recovery_uses_observation_not_scenario_label(self):
        class ObservedOutage(ReliabilityTrial):
            def station_available(self, t):
                return not 10 <= t < 45
        labelled = ReliabilityTrial(3, "tool_outage", True).run()
        unlabelled = ObservedOutage(3, "clean", True).run()
        labelled.pop("scenario")
        unlabelled.pop("scenario")
        self.assertEqual(labelled, unlabelled)

    def test_invalid_messages_fail_closed(self):
        sim = ReliabilityTrial(protected=True)
        sim.receive(0, "W0", "DONE", 999, version=1, lease=20)
        sim.receive(0, "W1", "DELEGATE", 0)
        sim.receive(0, "W1", "UNKNOWN_COMMAND", 0)
        self.assertEqual(sim.rejects, 3)
        self.assertEqual(len(sim.state), 12)
        self.assertFalse(sim.accepted)
        self.assertTrue(all(v == "new" for v in sim.state.values()))

    def test_expired_lease_cannot_confirm_work(self):
        sim = ReliabilityTrial(protected=True)
        sim.owner[0], sim.lease[0], sim.state[0] = "W0", 20, "service"
        sim.physical.add(0)
        sim.receive(21, "W0", "DONE", 0, version=1, lease=20)
        self.assertFalse(sim.accepted)
        self.assertEqual(sim.credits, 0)
        self.assertEqual(sim.rejects, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
