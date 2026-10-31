"""Tests for pulso.evrp. All costs here are SYNTHETIC (tiny hand-made worlds)."""

import json

import numpy as np
import pytest

from pulso.evrp import (
    ConstantCostProvider, FreeFlowCostProvider, Fleet, Problem, Solution, Stop,
    SyntheticCostProvider, compare, make_demo_problem, simulate, solve, slot_of,
)

TL = 1.5   # seconds per solver round in tiny tests


def tiny(stops, n_trucks=1, cap=1000.0, batt=100.0, reserve=0.0, **kw):
    return Problem(depot=(0.0, 0.0), stops=stops,
                   fleet=Fleet(n_trucks, cap, batt, reserve),
                   shift_start_min=360.0, shift_end_min=1200.0, **kw)


# 1 km per minute, 1 kWh per km
UNIT = ConstantCostProvider(speed_kmh=60.0, kwh_per_km=1.0)


def test_known_optimum_line():
    stops = [Stop("A", 3, 0), Stop("B", 1, 0), Stop("C", 2, 0)]
    sol = solve(tiny(stops), UNIT, TL, max_rounds=1)
    assert not sol.unserved and len(sol.trucks) == 1
    assert sol.trucks[0].stop_order in (["B", "C", "A"], ["A", "C", "B"])
    assert sol.total_kwh == pytest.approx(6.0, abs=1e-6)


def test_two_sides_one_truck_vs_capacity_split():
    stops = [Stop("E", 5, 0, demand_kg=60), Stop("W", -5, 0, demand_kg=60)]
    one = solve(tiny(stops, 2, cap=200), UNIT, TL, max_rounds=1)
    assert len(one.trucks) in (1, 2)
    assert one.total_kwh == pytest.approx(20.0, abs=1e-6)   # 10 out+back either way
    two = solve(tiny(stops, 2, cap=100), UNIT, TL, max_rounds=1)
    assert len(two.trucks) == 2                              # capacity forces a split
    for t in two.trucks:
        assert t.load_kg <= 100 + 1e-9
    assert not two.unserved


def test_capacity_unserved_when_stop_too_heavy():
    stops = [Stop("H", 1, 0, demand_kg=500), Stop("L", 2, 0, demand_kg=10)]
    sol = solve(tiny(stops, 1, cap=100), UNIT, TL, max_rounds=1)
    assert sol.unserved == ["H"]


def test_time_windows_force_order():
    # A is closer but opens late; B is farther and closes early.
    stops = [Stop("A", 1, 0, tw_open=600, tw_close=700, service_min=5),
             Stop("B", 4, 0, tw_open=400, tw_close=450, service_min=5)]
    sol = solve(tiny(stops), UNIT, TL, max_rounds=1)
    assert not sol.unserved
    assert sol.trucks[0].stop_order == ["B", "A"]
    for v in sol.trucks[0].visits:
        s = next(s for s in stops if s.id == v.stop_id)
        assert v.late_min == 0
        assert s.tw_open - 1e-9 <= v.service_start_min <= s.tw_close + 1e-9


def test_battery_forces_two_trucks_and_reserve_respected():
    stops = [Stop("E", 6, 0), Stop("W", -6, 0)]
    pr = tiny(stops, 2, batt=20.0, reserve=5.0)       # 24 kWh in one tour > 15 usable
    sol = solve(pr, UNIT, TL, max_rounds=1)
    assert not sol.unserved and len(sol.trucks) == 2
    for t in sol.trucks:
        assert t.min_soc_kwh >= 5.0 - 1e-6
        assert not t.below_reserve and not t.ran_out
    assert sol.total_kwh == pytest.approx(24.0, abs=1e-6)


def test_battery_unreachable_stop_is_unserved():
    stops = [Stop("FAR", 30, 0), Stop("NEAR", 2, 0)]
    sol = solve(tiny(stops, 1, batt=50.0, reserve=0.0), UNIT, TL, max_rounds=1)
    assert sol.unserved == ["FAR"]


class RegenWorld:
    """SYNTHETIC: legs into the depot regenerate (-3 kWh), others cost 5."""
    name = "SYNTHETIC regen world"
    synthetic = True

    def distances_km(self, locations):
        n = len(locations)
        return 5.0 * (np.ones((n, n)) - np.eye(n))

    def matrices(self, locations, slot, day_type, vehicle_type="truck_electric"):
        n = len(locations)
        off = np.ones((n, n)) - np.eye(n)
        e = 5.0 * off
        e[1:, 0] = -3.0
        return 10.0 * off, e


def test_regeneration_negative_energy_handled():
    pr = tiny([Stop("A", 1, 0), Stop("B", 2, 0)], 1, batt=11.0, reserve=0.0)
    sol = solve(pr, RegenWorld(), TL, max_rounds=1)
    assert not sol.unserved
    assert sol.total_kwh == pytest.approx(7.0, abs=1e-6)     # 5 + 5 - 3, net of regeneration
    assert simulate(sol, RegenWorld()).total_kwh == pytest.approx(7.0, abs=1e-6)
    # reserve above what is left after the second stop must make it unserved/split
    pr2 = tiny([Stop("A", 1, 0), Stop("B", 2, 0)], 1, batt=11.0, reserve=3.0)
    sol2 = solve(pr2, RegenWorld(), TL, max_rounds=1)
    assert sol2.unserved or all(t.min_soc_kwh >= 3.0 - 1e-6 for t in sol2.trucks)


def test_simulate_reproduces_plan_under_constant_costs():
    pr = make_demo_problem(n_stops=20, n_trucks=3, seed=3)
    sol = solve(pr, UNIT, 3.0, max_rounds=1)
    sim = simulate(sol, UNIT)
    assert len(sim.trucks) == len(sol.trucks)
    for a, b in zip(sol.trucks, sim.trucks):
        assert a.stop_order == b.stop_order
        assert b.energy_kwh == pytest.approx(a.energy_kwh, abs=1e-6)
        assert b.min_soc_kwh == pytest.approx(a.min_soc_kwh, abs=1e-6)
        for va, vb in zip(a.visits, b.visits):
            assert vb.arrival_min == pytest.approx(va.arrival_min, abs=1e-6)
            assert vb.late_min == pytest.approx(va.late_min, abs=1e-6)
    assert sim.total_kwh == pytest.approx(sol.total_kwh, abs=1e-6)


class PeakWorld:
    """SYNTHETIC two-point world: slots 24..39 (06:00-10:00) are a peak with
    3x travel time and 2x energy; otherwise 20 min and 10 kWh per leg."""
    name = "SYNTHETIC peak world"
    synthetic = True

    def distances_km(self, locations):
        n = len(locations)
        return 10.0 * (np.ones((n, n)) - np.eye(n))

    def matrices(self, locations, slot, day_type, vehicle_type="truck_electric"):
        n = len(locations)
        peak = 24 <= slot < 40
        off = np.ones((n, n)) - np.eye(n)
        return off * (60.0 if peak else 20.0), off * (20.0 if peak else 10.0)


def test_free_flow_plan_strands_truck_informed_does_not():
    pr = Problem(depot=(0, 0), stops=[Stop("X", 1, 0, service_min=10, tw_open=420, tw_close=720)],
                 fleet=Fleet(1, 1000, battery_kwh=24.0, reserve_kwh=0.0),
                 shift_start_min=360, shift_end_min=1200)
    real = PeakWorld()
    cmp = compare(pr, real, time_limit_s=TL)
    ff = cmp.simulations["free_flow_real"]
    inf = cmp.simulations["informed_real"]
    # Free-flow believes 20 kWh and leaves just-in-time into the peak...
    assert cmp.solutions["free_flow"].total_kwh == pytest.approx(20.0, abs=1e-6)
    assert ff.trucks_stranded == 1
    assert ff.trucks[0].first_failure in ("depot", "X")
    # ...the informed plan waits for the off-peak and keeps its battery.
    assert inf.trucks_stranded == 0 and inf.minutes_late == 0
    assert inf.min_soc_kwh >= 0
    assert inf.trucks[0].depart_min > ff.trucks[0].depart_min
    assert inf.total_kwh == pytest.approx(20.0, abs=1e-6)
    assert list(cmp.table.columns)[:8] == ["plan", "total_kwh", "total_km", "total_hours",
                                            "late_deliveries", "minutes_late",
                                            "trucks_stranded", "trucks_below_reserve"]
    assert len(cmp.table) == 4 and len(cmp.per_truck) >= 2


def test_free_flow_late_under_peak():
    # Free flow believes 20 min of travel and leaves at 06:45 to arrive at 07:05;
    # in the real peak the leg takes 60 min, so it arrives 07:45 > 07:20.
    pr = Problem(depot=(0, 0), stops=[Stop("X", 1, 0, service_min=5, tw_open=420, tw_close=440)],
                 fleet=Fleet(1, 1000, battery_kwh=1000.0),
                 shift_start_min=360, shift_end_min=1200)
    cmp = compare(pr, PeakWorld(), time_limit_s=TL)
    ff = cmp.simulations["free_flow_real"]
    assert ff.late_deliveries == 1 and ff.minutes_late == pytest.approx(25.0)
    inf = cmp.simulations["informed_real"]
    assert inf.late_deliveries == 0 and inf.minutes_late == 0


def test_free_flow_provider_uses_fastest_slot():
    base = SyntheticCostProvider()
    ff = FreeFlowCostProvider(base)
    loc = [(0, 0), (5, 5), (-4, 3)]
    t_ff, e_ff = ff.matrices(loc, 32, "weekday")
    t_peak, _ = base.matrices(loc, 32, "weekday")
    assert (t_ff <= t_peak + 1e-9).all() and (t_ff < t_peak).any()
    for s in (0, 20, 50, 80):
        assert base.matrices(loc, s, "weekday")[0].sum() >= t_ff.sum() - 1e-9
    assert ff.synthetic


def test_synthetic_provider_shape():
    p = SyntheticCostProvider()
    assert p.synthetic and "SYNTHETIC" in p.name
    loc = [(0, 0), (3, 0), (0, 3), (0, -3)]
    t8, e8 = p.matrices(loc, slot_of(8 * 60), "weekday")
    t13, e13 = p.matrices(loc, slot_of(13 * 60), "weekday")
    assert (t8[0, 1:] > t13[0, 1:]).all() and (e8[0, 1:] > e13[0, 1:]).all()
    # uphill is +y by default: 0->(0,3) costs more than 0->(0,-3)
    assert e13[0, 2] > e13[0, 3]
    assert np.allclose(np.diag(t8), 0) and np.allclose(np.diag(e8), 0)


def test_solution_json_roundtrip():
    pr = make_demo_problem(n_stops=12, n_trucks=2, seed=1)
    sol = solve(pr, SyntheticCostProvider(), 2.0, max_rounds=1)
    text = sol.to_json()
    d = json.loads(text)
    assert d["synthetic_costs"] is True
    tr = d["trucks"][0]
    for key in ("depart_min", "stops"):
        assert key in tr
    for key in ("stop_id", "arrival_min", "departure_min", "soc_kwh", "cum_kwh", "x", "y"):
        assert key in tr["stops"][0]
    back = Solution.from_json(text)
    assert back.to_dict() == sol.to_dict()
    # a restored plan can be simulated
    sim = simulate(back, SyntheticCostProvider())
    assert sim.total_kwh == pytest.approx(simulate(sol, SyntheticCostProvider()).total_kwh)
