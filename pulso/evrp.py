"""Time- and energy-aware vehicle routing for an electric delivery fleet (C6).

WARNING ABOUT NUMBERS
---------------------
Everything produced with ``SyntheticCostProvider`` or ``ConstantCostProvider``
is SYNTHETIC: invented parameters, not measurements. Such numbers demonstrate
that the machinery works; they are not results. Only a provider fed by the
bus-speed atlas can produce results.

What this module does
---------------------
* ``CostProvider`` is the only thing the optimiser knows about costs: for a
  list of locations, a 15-minute slot, a day type and a vehicle type it returns
  two matrices (travel minutes, energy in kWh). Travel between two points
  depends on the departure slot.
* ``solve`` plans one depot, N stops (time window, service time, demand in kg)
  and K identical trucks (payload, usable battery, minimum reserve), each truck
  leaving at a departure time of its own, minimising total energy.
* ``simulate`` replays a fixed plan (stop order + departure times) leg by leg
  under any provider, using the real slot at each actual departure. Plan with
  ``FreeFlowCostProvider(real)``, simulate under ``real``: that is the honest
  comparison (``compare``).

How time dependence is handled (and its limits)
-----------------------------------------------
OR-Tools routing cannot make an arc cost depend on the cumulative time. We use
"iterated band-consistent solving plus exact departure search":

1. Every truck gets its own transit matrices (OR-Tools supports per-vehicle
   evaluators). Round 0 uses the shift-average matrices for everyone.
2. OR-Tools (parallel cheapest insertion + guided local search) solves the
   routing problem with those fixed matrices: capacity, hard time windows,
   battery bound, shift limits, minimal energy.
3. For each resulting route the order is frozen and the departure time is
   chosen by exhaustive search over the departure grid (default 15 min) with an
   EXACT time-dependent replay (the slot of every leg is taken from the real
   clock, waiting at early arrivals included). Lexicographic score: battery
   violation, minutes late, overtime, kWh, idle waiting, earliest departure. This is where
   "leave at 10:30 instead of 08:00" comes from.
4. Each truck's matrix is replaced by the mean matrix over the slots its route
   actually spans (departure to return). Trucks whose exact replay is late or
   under reserve get their travel times padded by 5 % (and the battery bound
   tightened by the deficit) for the next round. Rounds repeat, warm-started
   from the previous routes, until no truck changes band or the time is up.
   The best round by exact score is returned.

Limits: (a) a route is solved with ONE slot's costs, so within a long route the
solver sees stale costs; the exact replay corrects the reported numbers and the
departure, but the stop order is only locally consistent. (b) Routes are not
guaranteed to converge; the padding heuristic usually fixes violations but
cannot prove feasibility. Violations remaining after the exact replay are
reported in the solution, never hidden. (c) The truck/stop assignment is not
jointly optimised with departure times (departure is optimised per fixed
route). (d) Payload does not change energy (matrices depend on vehicle type
only). (e) Times beyond midnight are clamped to the last slot.

Regeneration (negative kWh legs)
--------------------------------
Routing dimensions need non-negative transits, so arc costs are shifted with
node potentials (Johnson reweighting): ``w'_ij = w_ij + p_i - p_j >= 0`` where
``p`` is a shortest-path potential of the elementwise-minimum energy matrix.
Potentials telescope on every closed tour, so the objective equals the true
net kWh exactly (up to 1 Wh rounding) and the battery bound becomes a
per-node bound ``cum_i <= cap - reserve + p_0 - p_i``. If the matrices contain
a negative cycle (physically impossible, possible with noisy data) negative
legs are clamped to zero (conservative). The solver ignores the "battery full,
cannot store regen" clamp; the exact replay applies it (state of charge is
capped at full capacity).

Charging
--------
NOT IMPLEMENTED. Problem has no charging stations: a truck starts at the depot
with a full usable battery and must finish the route above its reserve. This
was cut deliberately to keep the no-charging problem solid.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Optional, Protocol, Sequence, runtime_checkable

import numpy as np
import pandas as pd

SLOT_MIN = 15
N_SLOTS = 96
TIME_SCALE = 10          # solver time unit = 0.1 min
ENERGY_SCALE = 1000      # solver energy unit = 1 Wh
EPS = 1e-6
BAND_MARGIN_MIN = 45     # route bands are widened by this much on both sides

Location = tuple  # (x, y). Synthetic provider: km in a plane. Real: (lon, lat).


def slot_of(minute: float) -> int:
    """15-minute slot (0..95) containing ``minute`` since midnight (clamped)."""
    return int(min(N_SLOTS - 1, max(0, minute // SLOT_MIN)))


def fmt_hhmm(minute: float) -> str:
    m = int(round(minute))
    return f"{m // 60:02d}:{m % 60:02d}"


# --------------------------------------------------------------------------
# Cost provider interface
# --------------------------------------------------------------------------

@runtime_checkable
class CostProvider(Protocol):
    """Everything the optimiser knows about costs.

    ``synthetic`` must be True for any provider not backed by real data.
    """

    name: str
    synthetic: bool

    def matrices(self, locations: Sequence[Location], slot: int, day_type: str,
                 vehicle_type: str = "truck_electric") -> tuple[np.ndarray, np.ndarray]:
        """Return (minutes, kwh), each LxL, entry [i, j] = leg i -> j when the
        vehicle departs i during 15-minute ``slot`` (0..95). Diagonal is 0.
        kWh may be negative (downhill with regeneration)."""
        ...

    def distances_km(self, locations: Sequence[Location]) -> np.ndarray:
        """LxL driven distance in km (time independent)."""
        ...


@dataclass
class SyntheticParams:
    """Explicit parameters of the SYNTHETIC world. Not measurements."""
    centre: tuple = (0.0, 0.0)        # km, congested core
    centre_sigma_km: float = 8.0      # congestion decays as exp(-(d/sigma)^2)
    circuity: float = 1.3             # road length / straight line
    free_speed_kmh: float = 45.0      # outskirts, no traffic
    am_peak_h: float = 8.0
    am_peak_amp: float = 1.8          # extra slowdown factor at the AM peak
    am_peak_width_h: float = 1.25
    pm_peak_h: float = 18.0
    pm_peak_amp: float = 2.0
    pm_peak_width_h: float = 1.5
    base_congestion: float = 0.05     # all-day slowdown in the core
    outskirt_share: float = 0.25      # share of peak slowdown felt far from centre
    # kWh/km = a0 + a_aero*v^2 + a_stop/v   (v in km/h)
    a0: float = 0.60
    a_aero: float = 0.00012
    a_stop: float = 20.0
    # climb: kWh per metre of ascent (8 t truck ~ 0.0218 kWh/m at 100 % eff.)
    kwh_per_m_climb: float = 0.0250
    regen_efficiency: float = 0.55    # share of descent energy recovered
    uphill_dir_deg: float = 90.0      # terrain rises towards this direction
    grade: float = 0.012              # 1.2 % average rise along uphill_dir


class SyntheticCostProvider:
    """SYNTHETIC cost world for tests and demo: a plane in km with a congested
    centre, AM/PM peaks that slow traffic and raise kWh/km, and a terrain that
    rises along one direction (uphill costs energy, downhill regenerates).
    NOT data. Never quote its numbers as results."""

    synthetic = True

    def __init__(self, params: Optional[SyntheticParams] = None):
        self.params = params or SyntheticParams()
        self.name = "SYNTHETIC (invented parameters, not data)"

    def _xy(self, locations):
        a = np.asarray(locations, dtype=float).reshape(-1, 2)
        return a[:, 0], a[:, 1]

    def distances_km(self, locations):
        x, y = self._xy(locations)
        d = np.hypot(x[:, None] - x[None, :], y[:, None] - y[None, :])
        return d * self.params.circuity

    def _peak(self, slot: int) -> float:
        p = self.params
        t = (slot + 0.5) * SLOT_MIN / 60.0
        return (p.am_peak_amp * math.exp(-((t - p.am_peak_h) / p.am_peak_width_h) ** 2)
                + p.pm_peak_amp * math.exp(-((t - p.pm_peak_h) / p.pm_peak_width_h) ** 2))

    def _elevation_m(self, x, y):
        p = self.params
        th = math.radians(p.uphill_dir_deg)
        return p.grade * 1000.0 * (x * math.cos(th) + y * math.sin(th))

    def matrices(self, locations, slot, day_type, vehicle_type="truck_electric"):
        p = self.params
        x, y = self._xy(locations)
        dist = self.distances_km(locations)
        mx = (x[:, None] + x[None, :]) / 2
        my = (y[:, None] + y[None, :]) / 2
        d_c = np.hypot(mx - p.centre[0], my - p.centre[1])
        g = np.exp(-(d_c / p.centre_sigma_km) ** 2)
        peak = self._peak(slot)
        if day_type != "weekday":
            peak *= 0.3        # weekends: much milder peaks (still synthetic)
        slow = p.base_congestion * g + peak * (p.outskirt_share + (1 - p.outskirt_share) * g)
        v = p.free_speed_kmh / (1.0 + slow)
        minutes = np.where(dist > 0, dist / v * 60.0, 0.0)
        rate = p.a0 + p.a_aero * v ** 2 + p.a_stop / v
        flat = dist * rate
        dh = self._elevation_m(x[None, :], y[None, :]) - self._elevation_m(x[:, None], y[:, None])
        climb = np.where(dh > 0, dh * p.kwh_per_m_climb, dh * p.kwh_per_m_climb * p.regen_efficiency)
        kwh = flat + climb
        np.fill_diagonal(minutes, 0.0)
        np.fill_diagonal(kwh, 0.0)
        return minutes, kwh


class ConstantCostProvider:
    """Time-independent costs (straight-line km x circuity at a constant speed
    and kWh/km). SYNTHETIC; used by tests and to verify the machinery."""

    synthetic = True

    def __init__(self, speed_kmh=30.0, kwh_per_km=1.0, circuity=1.0):
        self.speed, self.rate, self.circ = speed_kmh, kwh_per_km, circuity
        self.name = f"SYNTHETIC constant ({speed_kmh} km/h, {kwh_per_km} kWh/km)"

    def distances_km(self, locations):
        a = np.asarray(locations, float).reshape(-1, 2)
        return np.hypot(a[:, None, 0] - a[None, :, 0], a[:, None, 1] - a[None, :, 1]) * self.circ

    def matrices(self, locations, slot, day_type, vehicle_type="truck_electric"):
        d = self.distances_km(locations)
        return d / self.speed * 60.0, d * self.rate


class FreeFlowCostProvider:
    """Naive-planner view of a provider: always answers with its fastest slot
    (smallest total off-diagonal travel time over the day), whatever slot is
    asked. Distances are passed through."""

    def __init__(self, base: CostProvider, slots: Optional[Sequence[int]] = None):
        self.base = base
        self.slots = list(slots) if slots is not None else list(range(N_SLOTS))
        self.synthetic = getattr(base, "synthetic", False)
        self.name = f"free-flow({getattr(base, 'name', 'provider')})"
        self._best: dict = {}
        self._mats: dict = {}

    def distances_km(self, locations):
        return self.base.distances_km(locations)

    def fastest_slot(self, locations, day_type, vehicle_type="truck_electric") -> int:
        key = (np.asarray(locations, float).tobytes(), day_type, vehicle_type)
        if key not in self._best:
            tot = [float(self.base.matrices(locations, s, day_type, vehicle_type)[0].sum())
                   for s in self.slots]
            self._best[key] = self.slots[int(np.argmin(tot))]
        return self._best[key]

    def matrices(self, locations, slot, day_type, vehicle_type="truck_electric"):
        s = self.fastest_slot(locations, day_type, vehicle_type)
        key = (np.asarray(locations, float).tobytes(), day_type, vehicle_type)
        if key not in self._mats:
            self._mats[key] = self.base.matrices(locations, s, day_type, vehicle_type)
        return self._mats[key]


class _MatrixCache:
    """Lazy per-slot matrices for one fixed list of locations."""

    def __init__(self, provider, locations, day_type, vehicle_type):
        self.p, self.loc, self.day, self.veh = provider, list(locations), day_type, vehicle_type
        self._c: dict = {}
        self.dist = np.asarray(provider.distances_km(self.loc), float)

    def get(self, slot: int):
        if slot not in self._c:
            t, e = self.p.matrices(self.loc, slot, self.day, self.veh)
            self._c[slot] = (np.asarray(t, float), np.asarray(e, float))
        return self._c[slot]


# --------------------------------------------------------------------------
# Problem data
# --------------------------------------------------------------------------

@dataclass
class Stop:
    id: str
    x: float
    y: float
    demand_kg: float = 0.0
    service_min: float = 5.0
    tw_open: float = 0.0        # minutes since midnight
    tw_close: float = 1440.0    # service must START by this time


@dataclass
class Fleet:
    n_trucks: int
    capacity_kg: float
    battery_kwh: float          # usable energy at departure (full)
    reserve_kwh: float = 0.0    # minimum state of charge allowed at any arrival
    vehicle_type: str = "truck_electric"


@dataclass
class Problem:
    depot: tuple                      # (x, y)
    stops: list
    fleet: Fleet
    day_type: str = "weekday"
    shift_start_min: float = 360.0    # trucks may leave from here...
    shift_end_min: float = 1200.0     # ...and must be back by here
    departure_step_min: float = 15.0
    depot_name: str = "depot"

    def locations(self) -> list:
        return [tuple(self.depot)] + [(s.x, s.y) for s in self.stops]

    def to_dict(self):
        return {"depot": list(self.depot), "depot_name": self.depot_name,
                "stops": [asdict(s) for s in self.stops], "fleet": asdict(self.fleet),
                "day_type": self.day_type, "shift_start_min": self.shift_start_min,
                "shift_end_min": self.shift_end_min,
                "departure_step_min": self.departure_step_min}

    @staticmethod
    def from_dict(d):
        return Problem(depot=tuple(d["depot"]), stops=[Stop(**s) for s in d["stops"]],
                       fleet=Fleet(**d["fleet"]), day_type=d["day_type"],
                       shift_start_min=d["shift_start_min"], shift_end_min=d["shift_end_min"],
                       departure_step_min=d["departure_step_min"],
                       depot_name=d.get("depot_name", "depot"))


# --------------------------------------------------------------------------
# Exact leg-by-leg replay
# --------------------------------------------------------------------------

@dataclass
class StopVisit:
    stop_id: str
    x: float
    y: float
    demand_kg: float
    arrival_min: float
    service_start_min: float
    departure_min: float
    late_min: float
    soc_kwh: float          # battery on arrival
    cum_kwh: float          # net kWh since leaving the depot (arrival)


@dataclass
class TruckPlan:
    truck: int
    depart_min: float
    visits: list                 # list[StopVisit]
    return_min: float
    soc_end_kwh: float
    energy_kwh: float            # net kWh for the whole route
    km: float
    min_soc_kwh: float
    late_total_min: float
    overtime_min: float
    load_kg: float
    ran_out: bool                # soc < 0 somewhere
    below_reserve: bool
    first_failure: Optional[str]  # stop id or "depot" where soc first < reserve
    wait_min: float = 0.0        # idle minutes waiting for windows to open

    @property
    def stop_order(self):
        return [v.stop_id for v in self.visits]


def _replay(problem: Problem, cache: _MatrixCache, nodes: Sequence[int], depart: float):
    """Replay a route (``nodes`` = 1-based stop indices) departing the depot at
    ``depart``. Slot of every leg = slot of the actual departure time of that
    leg. Returns a TruckPlan with truck=-1."""
    fl = problem.fleet
    cap = fl.battery_kwh
    t, soc, cum, km = depart, cap, 0.0, 0.0
    node, visits = 0, []
    min_soc, fail, late_tot, wait_tot = cap, None, 0.0, 0.0
    ran_out = below = False

    def check(label, soc_now):
        nonlocal min_soc, fail, ran_out, below
        min_soc = min(min_soc, soc_now)
        if soc_now < -EPS:
            ran_out = True
        if soc_now < fl.reserve_kwh - EPS:
            below = True
            if fail is None:
                fail = label

    for j in nodes:
        T, E = cache.get(slot_of(t))
        s = problem.stops[j - 1]
        arr = t + T[node, j]
        e = E[node, j]
        soc = min(cap, soc - e)
        cum += e
        km += cache.dist[node, j]
        start = max(arr, s.tw_open)
        wait_tot += start - arr
        late = max(0.0, arr - s.tw_close)
        late = 0.0 if late < 1e-6 else late
        late_tot += late
        check(s.id, soc)
        t = start + s.service_min
        visits.append(StopVisit(s.id, s.x, s.y, s.demand_kg, arr, start, t, late, soc, cum))
        node = j
    T, E = cache.get(slot_of(t))
    arr = t + T[node, 0]
    soc = min(cap, soc - E[node, 0])
    cum += E[node, 0]
    km += cache.dist[node, 0]
    check("depot", soc)
    overtime = max(0.0, arr - problem.shift_end_min)
    overtime = 0.0 if overtime < 1e-6 else overtime
    return TruckPlan(-1, depart, visits, arr, soc, cum, km, min_soc, late_tot, overtime,
                     sum(problem.stops[j - 1].demand_kg for j in nodes),
                     ran_out, below, fail, wait_tot)


def _score(problem: Problem, plan: TruckPlan):
    deficit = max(0.0, problem.fleet.reserve_kwh - plan.min_soc_kwh)
    return (round(deficit, 3), round(plan.late_total_min, 3), round(plan.overtime_min, 3),
            round(plan.energy_kwh, 4), round(plan.wait_min, 2), plan.depart_min)


def _best_departure(problem: Problem, cache: _MatrixCache, nodes: Sequence[int],
                    extra: Sequence[float] = ()) -> TruckPlan:
    """Exhaustive search of the departure time on the grid (plus the solver's
    own start times in ``extra``)."""
    step = max(1.0, problem.departure_step_min)
    best, best_s = None, None
    cands = list(extra)
    t = problem.shift_start_min
    while t <= problem.shift_end_min + EPS:
        cands.append(t)
        t += step
    for t in cands:
        pl = _replay(problem, cache, nodes, t)
        sc = _score(problem, pl)
        if best_s is None or sc < best_s:
            best, best_s = pl, sc
    return best


# --------------------------------------------------------------------------
# Solution + JSON
# --------------------------------------------------------------------------

FORMAT = "pulso.evrp.solution/1"


@dataclass
class Solution:
    problem: Problem
    trucks: list                      # list[TruckPlan], non-empty routes only
    unserved: list                    # stop ids
    provider_name: str
    synthetic_costs: bool
    solve_seconds: float = 0.0
    iterations: int = 0
    notes: list = field(default_factory=list)

    @property
    def total_kwh(self):
        return sum(t.energy_kwh for t in self.trucks)

    def to_dict(self, include_problem: bool = True) -> dict:
        d = {
            "format": FORMAT,
            "synthetic_costs": self.synthetic_costs,
            "provider": self.provider_name,
            "day_type": self.problem.day_type,
            "summary": {
                "total_kwh": self.total_kwh,
                "total_km": sum(t.km for t in self.trucks),
                "trucks_used": len(self.trucks),
                "unserved": list(self.unserved),
                "late_minutes": sum(t.late_total_min for t in self.trucks),
                "solve_seconds": self.solve_seconds,
                "iterations": self.iterations,
            },
            "notes": list(self.notes),
            "depot": {"name": self.problem.depot_name, "x": self.problem.depot[0],
                      "y": self.problem.depot[1]},
            "trucks": [],
        }
        for t in self.trucks:
            d["trucks"].append({
                "truck": t.truck, "depart_min": t.depart_min, "depart": fmt_hhmm(t.depart_min),
                "return_min": t.return_min, "return": fmt_hhmm(t.return_min),
                "soc_end_kwh": t.soc_end_kwh, "energy_kwh": t.energy_kwh, "km": t.km,
                "min_soc_kwh": t.min_soc_kwh, "late_total_min": t.late_total_min,
                "overtime_min": t.overtime_min, "load_kg": t.load_kg,
                "ran_out": t.ran_out, "below_reserve": t.below_reserve,
                "first_failure": t.first_failure, "wait_min": t.wait_min,
                "stops": [{"seq": i + 1, "stop_id": v.stop_id, "x": v.x, "y": v.y,
                           "demand_kg": v.demand_kg, "arrival_min": v.arrival_min,
                           "arrival": fmt_hhmm(v.arrival_min),
                           "service_start_min": v.service_start_min,
                           "departure_min": v.departure_min, "late_min": v.late_min,
                           "soc_kwh": v.soc_kwh, "cum_kwh": v.cum_kwh}
                          for i, v in enumerate(t.visits)],
            })
        if include_problem:
            d["problem"] = self.problem.to_dict()
        return d

    def to_json(self, **kw) -> str:
        return json.dumps(self.to_dict(), **kw)

    @staticmethod
    def from_dict(d: dict) -> "Solution":
        if d.get("format") != FORMAT:
            raise ValueError("unknown solution format")
        problem = Problem.from_dict(d["problem"])
        trucks = []
        for t in d["trucks"]:
            visits = [StopVisit(s["stop_id"], s["x"], s["y"], s["demand_kg"], s["arrival_min"],
                                s["service_start_min"], s["departure_min"], s["late_min"],
                                s["soc_kwh"], s["cum_kwh"]) for s in t["stops"]]
            trucks.append(TruckPlan(t["truck"], t["depart_min"], visits, t["return_min"],
                                    t["soc_end_kwh"], t["energy_kwh"], t["km"], t["min_soc_kwh"],
                                    t["late_total_min"], t["overtime_min"], t["load_kg"],
                                    t["ran_out"], t["below_reserve"], t["first_failure"],
                                    t.get("wait_min", 0.0)))
        s = d["summary"]
        return Solution(problem, trucks, list(s["unserved"]), d["provider"],
                        d["synthetic_costs"], s["solve_seconds"], s["iterations"],
                        list(d.get("notes", [])))

    @staticmethod
    def from_json(text: str) -> "Solution":
        return Solution.from_dict(json.loads(text))


# --------------------------------------------------------------------------
# simulate
# --------------------------------------------------------------------------

@dataclass
class SimResult:
    provider_name: str
    synthetic_costs: bool
    trucks: list            # list[TruckPlan] replayed under the provider
    unserved: list

    @property
    def total_kwh(self): return sum(t.energy_kwh for t in self.trucks)
    @property
    def total_km(self): return sum(t.km for t in self.trucks)
    @property
    def total_hours(self): return sum(t.return_min - t.depart_min for t in self.trucks) / 60.0
    @property
    def late_deliveries(self): return sum(1 for t in self.trucks for v in t.visits if v.late_min > EPS)
    @property
    def minutes_late(self): return sum(t.late_total_min for t in self.trucks)
    @property
    def trucks_stranded(self): return sum(1 for t in self.trucks if t.ran_out)
    @property
    def trucks_below_reserve(self): return sum(1 for t in self.trucks if t.below_reserve)
    @property
    def min_soc_kwh(self): return min((t.min_soc_kwh for t in self.trucks), default=float("nan"))


def simulate(solution: Solution, provider: CostProvider) -> SimResult:
    """Replay the plan (stop order + departure times of ``solution``) under
    ``provider``. Per leg the slot is that of the actual departure from the
    previous point; trucks that arrive early wait for the window to open. If a
    battery runs out the replay continues (state of charge goes negative) so
    the failure point and the deficit can be reported."""
    pr = solution.problem
    cache = _MatrixCache(provider, pr.locations(), pr.day_type, pr.fleet.vehicle_type)
    index = {s.id: i + 1 for i, s in enumerate(pr.stops)}
    out = []
    for t in solution.trucks:
        pl = _replay(pr, cache, [index[v.stop_id] for v in t.visits], t.depart_min)
        pl.truck = t.truck
        out.append(pl)
    return SimResult(getattr(provider, "name", "provider"), bool(getattr(provider, "synthetic", False)),
                     out, list(solution.unserved))


# --------------------------------------------------------------------------
# solve
# --------------------------------------------------------------------------

def _potentials(W: np.ndarray):
    """Shortest-path potentials p with W[i,j] + p[i] - p[j] >= 0, or None if
    W has a negative cycle."""
    n = W.shape[0]
    Wd = W.copy()
    np.fill_diagonal(Wd, np.inf)
    p = np.zeros(n)
    for _ in range(n + 1):
        q = np.minimum(p, (p[:, None] + Wd).min(axis=0))
        if np.all(q >= p - 1e-12):
            return p
        p = q
    return None


def _solve_round(problem, cache, veh_keys, mats, pads, margin, hint, time_limit_s, SC=TIME_SCALE):
    """One OR-Tools solve with per-vehicle fixed matrices. Returns
    (routes: list[list[int]], unserved: list[int]) or None."""
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    fl = problem.fleet
    n = len(problem.stops) + 1
    K = fl.n_trucks
    distinct = sorted(set(veh_keys), key=str)
    E_list = [mats[k][1] for k in distinct]
    Wmin = np.minimum.reduce(E_list)
    p = _potentials(Wmin)
    clamp = p is None
    if clamp:
        p = np.zeros(n)
    serv = np.array([0.0] + [s.service_min for s in problem.stops])

    manager = pywrapcp.RoutingIndexManager(n, K, 0)
    routing = pywrapcp.RoutingModel(manager)
    node_of = [manager.IndexToNode(i) for i in range(routing.Size())]
    # RegisterTransitMatrix is indexed by routing index (incl. vehicle start/end)
    nn = len(node_of)
    ixg = np.ix_(node_of, node_of)

    time_cb, en_cb = {}, {}
    for k in distinct:
        T = mats[k][0] * pads[k]
        E = mats[k][1]
        tt = np.ceil((T + serv[:, None]) * SC - 1e-9).astype(np.int64)
        red = E.copy()
        if clamp:
            red = np.maximum(red, 0.0)
        red = red + p[:, None] - p[None, :]
        ee = np.ceil(np.maximum(red, 0.0) * ENERGY_SCALE - 1e-9).astype(np.int64)
        time_cb[k] = routing.RegisterTransitMatrix(tt[ixg].tolist())
        en_cb[k] = routing.RegisterTransitMatrix(ee[ixg].tolist())

    # Time
    horizon = int(problem.shift_end_min * SC)
    routing.AddDimensionWithVehicleTransits([time_cb[k] for k in veh_keys], horizon, horizon,
                                            False, "Time")
    tdim = routing.GetDimensionOrDie("Time")
    for j, s in enumerate(problem.stops, start=1):
        idx = manager.NodeToIndex(j)
        lo = int(math.ceil(s.tw_open * SC - 1e-9))
        hi = int(math.floor(s.tw_close * SC + 1e-9))
        if hi < lo:
            hi = lo
        tdim.CumulVar(idx).SetRange(lo, hi)
    for v in range(K):
        tdim.CumulVar(routing.Start(v)).SetRange(int(problem.shift_start_min * SC), horizon)
        tdim.CumulVar(routing.End(v)).SetRange(int(problem.shift_start_min * SC), horizon)

    # Energy (reweighted, non-negative) with per-node battery bound
    big = int(1e9)
    routing.AddDimensionWithVehicleTransits([en_cb[k] for k in veh_keys], 0, big, True, "Energy")
    edim = routing.GetDimensionOrDie("Energy")
    avail = fl.battery_kwh - fl.reserve_kwh - margin
    infeasible_nodes = []
    for j in range(1, n):
        idx = manager.NodeToIndex(j)
        ub = int(math.floor((avail + p[0] - p[j]) * ENERGY_SCALE + 1e-9))
        if ub < 0:
            infeasible_nodes.append(j)
        else:
            edim.CumulVar(idx).SetRange(0, ub)
    for v in range(K):
        edim.CumulVar(routing.End(v)).SetRange(0, max(0, int(math.floor(avail * ENERGY_SCALE + 1e-9))))
        routing.SetArcCostEvaluatorOfVehicle(en_cb[veh_keys[v]], v)

    # Capacity
    dem = [0] + [int(math.ceil(s.demand_kg)) for s in problem.stops]
    dcb = routing.RegisterUnaryTransitCallback(lambda i: dem[manager.IndexToNode(i)])
    routing.AddDimensionWithVehicleCapacity(dcb, 0, [int(fl.capacity_kg)] * K, True, "Load")

    penalty = int(1e8)
    for j in range(1, n):
        idx = manager.NodeToIndex(j)
        routing.AddDisjunction([idx], penalty)
        if j in infeasible_nodes:
            routing.ActiveVar(idx).SetValue(0)

    prm = pywrapcp.DefaultRoutingSearchParameters()
    prm.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    prm.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    prm.time_limit.FromMilliseconds(max(200, int(time_limit_s * 1000)))
    routing.CloseModelWithParameters(prm)

    sol = None
    if hint:
        init = routing.ReadAssignmentFromRoutes(hint, True)
        if init is not None:
            sol = routing.SolveFromAssignmentWithParameters(init, prm)
    if sol is None:
        sol = routing.SolveWithParameters(prm)
    if sol is None:
        return None
    routes, served, starts = [], set(), []
    for v in range(K):
        idx = routing.Start(v)
        starts.append(sol.Value(tdim.CumulVar(idx)) / SC)
        r = []
        while not routing.IsEnd(idx):
            node = manager.IndexToNode(idx)
            if node != 0:
                r.append(node)
            idx = sol.Value(routing.NextVar(idx))
        routes.append(r)
        served.update(r)
    unserved = [j for j in range(1, n) if j not in served]
    return routes, unserved, starts


def _band(plan: TruckPlan) -> tuple:
    """Slots (first, last) the route spans, widened by BAND_MARGIN_MIN."""
    return (slot_of(plan.depart_min - BAND_MARGIN_MIN), slot_of(plan.return_min + BAND_MARGIN_MIN))


def _band_mats(cache: _MatrixCache, band: tuple):
    """Worst-case travel times (so windows hold wherever in the band the truck
    drives) and mean energy over the slots of ``band``."""
    ms = [cache.get(s) for s in range(band[0], band[1] + 1)]
    return np.max([m[0] for m in ms], axis=0), np.mean([m[1] for m in ms], axis=0)


def solve(problem: Problem, provider: CostProvider, time_limit_s: float = 30.0,
          max_rounds: int = 4, verbose: bool = False,
          warm_routes: Optional[Sequence[Sequence[str]]] = None) -> Solution:
    """Plan routes and departure times minimising total net kWh under
    ``provider`` (see module docstring for the method and its limits).

    ``warm_routes`` (lists of stop ids, one list per truck) seeds the search:
    they are evaluated exactly with the best departure time, kept as a
    candidate and used as the hint of round 0. ``compare`` seeds the informed
    planner with the free-flow routes, so it can only improve on them.

    Hard: capacity, time windows (service must start by tw_close), battery never
    below the reserve, return before shift end. Stops that cannot be served
    under these constraints are listed in ``Solution.unserved`` (heavy penalty)
    instead of failing. Reported numbers come from the exact replay, so
    violations left after the iteration show up in the solution."""
    t0 = time.time()
    pr = problem
    cache = _MatrixCache(provider, pr.locations(), pr.day_type, pr.fleet.vehicle_type)
    K = pr.fleet.n_trucks
    n = len(pr.stops) + 1
    # slot range relevant for the shift (mean matrices for round 0)
    s_lo, s_hi = slot_of(pr.shift_start_min), slot_of(min(1439, pr.shift_end_min + 60))
    slots = list(range(s_lo, s_hi + 1))
    mean_T = np.mean([cache.get(s)[0] for s in slots], axis=0)
    mean_E = np.mean([cache.get(s)[1] for s in slots], axis=0)

    veh_slot: list = [None] * K
    pad = [1.0] * K
    margin = 0.0
    hint = None
    best = None
    notes = []
    rounds_done = 0

    def make_key(cand):
        return (len(cand.unserved),
                sum(1 for t in cand.trucks if _score(pr, t)[:3] != (0.0, 0.0, 0.0)),
                round(sum(t.late_total_min + t.overtime_min for t in cand.trucks), 3),
                round(cand.total_kwh, 3))

    if warm_routes is not None:
        idx_of = {s.id: i + 1 for i, s in enumerate(pr.stops)}
        wr = [[idx_of[i] for i in r_] for r_ in warm_routes][:K]
        wr += [[] for _ in range(K - len(wr))]
        trucks = []
        for v, nodes in enumerate(wr):
            if nodes:
                pl = _best_departure(pr, cache, nodes)
                pl.truck = v
                trucks.append(pl)
        served = {j for r_ in wr for j in r_}
        cand = Solution(pr, trucks, [x.id for j, x in enumerate(pr.stops, 1) if j not in served],
                        getattr(provider, "name", "provider"), bool(getattr(provider, "synthetic", False)))
        best = (make_key(cand), cand)
        hint = wr
        notes.append("seeded with warm routes")

    for r in range(max_rounds):
        remaining = time_limit_s - (time.time() - t0)
        if remaining < 0.5 and best is not None and r > 0:
            break
        share = 0.45 if r == 0 else 0.55 / max(1, max_rounds - 1)
        budget = max(1.0, min(remaining, time_limit_s * share)) if r else max(1.0, time_limit_s * share)
        keys = [("mean", pad[v]) if veh_slot[v] is None else (veh_slot[v], pad[v]) for v in range(K)]
        mats = {}
        pads = {}
        for k in set(keys):
            mats[k] = (mean_T, mean_E) if k[0] == "mean" else _band_mats(cache, k[0])
            pads[k] = k[1]
        res = _solve_round(pr, cache, keys, mats, pads, margin, hint, budget)
        rounds_done += 1
        if res is None:
            notes.append(f"round {r}: solver returned no solution")
            break
        routes, unserved, starts = res
        trucks, changed, viol = [], False, False
        new_slot, new_pad = list(veh_slot), list(pad)
        for v, nodes in enumerate(routes):
            if not nodes:
                new_slot[v] = None
                continue
            pl = _best_departure(pr, cache, nodes, extra=[starts[v]])
            pl.truck = v
            trucks.append(pl)
            ms = _band(pl)
            if ms != veh_slot[v]:
                changed = True
            new_slot[v] = ms
            deficit = max(0.0, pr.fleet.reserve_kwh - pl.min_soc_kwh)
            if pl.late_total_min > EPS or pl.overtime_min > EPS or deficit > EPS:
                viol = True
                new_pad[v] = min(1.6, pad[v] * 1.05)
                margin = max(margin, min(deficit, 0.2 * pr.fleet.battery_kwh))
        cand = Solution(pr, trucks, [pr.stops[j - 1].id for j in unserved],
                        getattr(provider, "name", "provider"), bool(getattr(provider, "synthetic", False)))
        key = make_key(cand)
        if verbose:
            print(f"  round {r}: kwh={cand.total_kwh:.1f} unserved={len(cand.unserved)} "
                  f"viol={viol} changed={changed} t={time.time() - t0:.1f}s")
        if best is None or key < best[0]:
            best = (key, cand)
        hint = routes
        veh_slot = new_slot
        pad = new_pad
        if not changed and not viol:
            break
    if best is None:
        sol = Solution(pr, [], [s.id for s in pr.stops], getattr(provider, "name", "provider"),
                       bool(getattr(provider, "synthetic", False)), notes=notes + ["no solution"])
    else:
        sol = best[1]
        sol.notes = notes
    sol.iterations = rounds_done
    sol.solve_seconds = time.time() - t0
    if getattr(provider, "synthetic", False):
        sol.notes.append("SYNTHETIC costs: numbers are not results.")
    return sol


# --------------------------------------------------------------------------
# compare
# --------------------------------------------------------------------------

@dataclass
class Comparison:
    table: pd.DataFrame
    per_truck: pd.DataFrame
    solutions: dict          # {"free_flow": Solution, "informed": Solution}
    simulations: dict        # {"free_flow_real": SimResult, ...}
    synthetic_costs: bool


def _row(label, res: SimResult):
    return {"plan": label, "total_kwh": res.total_kwh, "total_km": res.total_km,
            "total_hours": res.total_hours, "late_deliveries": res.late_deliveries,
            "minutes_late": res.minutes_late, "trucks_stranded": res.trucks_stranded,
            "trucks_below_reserve": res.trucks_below_reserve,
            "min_soc_kwh": res.min_soc_kwh, "unserved": len(res.unserved)}


def _as_sim(sol: Solution) -> SimResult:
    return SimResult(sol.provider_name, sol.synthetic_costs, sol.trucks, sol.unserved)


def compare(problem: Problem, real_provider: CostProvider, time_limit_s: float = 30.0,
            verbose: bool = False) -> Comparison:
    """Plan with free-flow costs and with the real costs, then drive both plans
    under the real costs (``simulate``). Rows: what the free-flow planner
    believes, what really happens to its plan, and the informed plan driven.
    Both solves get ``time_limit_s``. Departure rule: lexicographic (violations, kWh, idle
    waiting, earliest). Under free flow all kWh tie, so the naive planner leaves
    just-in-time (least idle waiting), as a dispatcher scheduling from the first
    delivery would."""
    naive = FreeFlowCostProvider(real_provider)
    sol_ff = solve(problem, naive, time_limit_s, verbose=verbose)
    sol_in = solve(problem, real_provider, time_limit_s, verbose=verbose,
                   warm_routes=[t.stop_order for t in sol_ff.trucks])
    sim_ff = simulate(sol_ff, real_provider)
    sim_in = simulate(sol_in, real_provider)
    rows = [_row("free-flow plan, as planned (believed)", _as_sim(sol_ff)),
            _row("free-flow plan, driven in real traffic", sim_ff),
            _row("informed plan, as planned", _as_sim(sol_in)),
            _row("informed plan, driven in real traffic", sim_in)]
    table = pd.DataFrame(rows)
    det = []
    for tag, sim in (("free_flow", sim_ff), ("informed", sim_in)):
        for t in sim.trucks:
            det.append({"plan": tag, "truck": t.truck, "stops": len(t.visits),
                        "depart": fmt_hhmm(t.depart_min), "return": fmt_hhmm(t.return_min),
                        "km": t.km, "kwh": t.energy_kwh, "min_soc_kwh": t.min_soc_kwh,
                        "late_min": t.late_total_min, "overtime_min": t.overtime_min,
                        "ran_out": t.ran_out, "below_reserve": t.below_reserve,
                        "first_failure": t.first_failure, "load_kg": t.load_kg})
    return Comparison(table, pd.DataFrame(det), {"free_flow": sol_ff, "informed": sol_in},
                      {"free_flow_real": sim_ff, "informed_real": sim_in},
                      bool(getattr(real_provider, "synthetic", False)))


# --------------------------------------------------------------------------
# Demo
# --------------------------------------------------------------------------

def make_demo_problem(n_stops=150, n_trucks=12, seed=7) -> Problem:
    """SYNTHETIC delivery instance: stops spread over a 40 x 40 km plane around
    the congested centre, depot on the west side."""
    rng = np.random.default_rng(seed)
    stops = []
    for i in range(n_stops):
        x, y = rng.normal(0, 6, 2).clip(-14, 14)
        open_ = float(rng.choice([420, 480, 540, 600, 660, 720, 780, 840, 900]))
        width = float(rng.choice([120, 180, 240]))
        stops.append(Stop(f"S{i + 1:03d}", float(x), float(y),
                          demand_kg=float(rng.integers(30, 120)), service_min=8.0,
                          tw_open=open_, tw_close=min(open_ + width, 1080.0)))
    fleet = Fleet(n_trucks, capacity_kg=1500.0, battery_kwh=100.0, reserve_kwh=10.0)
    return Problem(depot=(-10.0, -5.0), stops=stops, fleet=fleet, day_type="weekday",
                   shift_start_min=360.0, shift_end_min=1200.0, depot_name="Depot (synthetic)")


def _print_comparison(cmp: Comparison):
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 30)
    print("=" * 78)
    print("SYNTHETIC COSTS: invented congestion/energy parameters, NOT data.")
    print("These numbers show that the machinery works; they are not results.")
    print("=" * 78)
    print(cmp.table.round(2).to_string(index=False))


def main(argv=None):
    ap = argparse.ArgumentParser(description="EVRP route optimiser (synthetic demo)")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--stops", type=int, default=150)
    ap.add_argument("--trucks", type=int, default=12)
    ap.add_argument("--time", type=float, default=30.0, help="seconds per planner")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if not a.demo:
        ap.print_help()
        return 0
    prob = make_demo_problem(a.stops, a.trucks, a.seed)
    real = SyntheticCostProvider()
    t0 = time.time()
    cmp = compare(prob, real, a.time, verbose=True)
    _print_comparison(cmp)
    print(f"\nsolve times: free-flow {cmp.solutions['free_flow'].solve_seconds:.1f}s, "
          f"informed {cmp.solutions['informed'].solve_seconds:.1f}s; total {time.time() - t0:.1f}s")
    out = Path(a.out) if a.out else Path(__file__).resolve().parent.parent / "outputs" / "evrp_demo.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "synthetic_costs": True,
        "warning": "SYNTHETIC costs: invented parameters, not data. Not results.",
        "comparison": json.loads(cmp.table.to_json(orient="records")),
        "per_truck": json.loads(cmp.per_truck.to_json(orient="records")),
        "informed_plan": cmp.solutions["informed"].to_dict(),
        "free_flow_plan": cmp.solutions["free_flow"].to_dict(),
    }
    out.write_text(json.dumps(payload), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
