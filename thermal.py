"""
thermal.py
Thermal model and thermal-aware scheduling policies.

Models the CPU die temperature using a simplified RC thermal circuit and
exposes hooks that the scheduler uses to throttle or migrate tasks.
"""

import math
from dataclasses import dataclass, field
from typing import List, Tuple, Optional


@dataclass
class ThermalZone:
    """Represents one thermal zone (e.g. CPU cluster, GPU, battery)."""
    name: str
    ambient_c: float = 25.0
    max_safe_c: float = 85.0
    throttle_c: float = 75.0
    thermal_resistance: float = 0.25   # °C / mW  (junction-to-ambient)
    thermal_capacitance: float = 5.0   # J / °C   (heat capacity)
    current_temp: float = field(init=False)
    temp_history: List[float] = field(default_factory=list, init=False)

    def __post_init__(self):
        self.current_temp = self.ambient_c
        self.temp_history.append(self.ambient_c)

    # ------------------------------------------------------------------ #
    #  RC thermal model:  dT/dt = (P - (T - T_amb)/R) / C               #
    # ------------------------------------------------------------------ #
    def step(self, power_mw: float, dt_ms: float = 10.0) -> float:
        """
        Advance the thermal model by `dt_ms` milliseconds.
        Returns updated temperature in °C.
        """
        dt_s = dt_ms / 1000.0
        t_ss = self.ambient_c + power_mw * self.thermal_resistance  # steady-state
        tau = self.thermal_resistance * self.thermal_capacitance
        # Exponential approach to steady state
        self.current_temp = (t_ss +
                             (self.current_temp - t_ss) * math.exp(-dt_s / tau))
        self.current_temp = round(min(self.current_temp, self.max_safe_c), 3)
        self.temp_history.append(self.current_temp)
        return self.current_temp

    @property
    def headroom(self) -> float:
        """Normalised headroom to throttle point: 1=cool, 0=at limit."""
        span = self.throttle_c - self.ambient_c
        return max(0.0, min(1.0, (self.throttle_c - self.current_temp) / span))

    @property
    def is_critical(self) -> bool:
        return self.current_temp >= self.max_safe_c

    @property
    def is_throttling(self) -> bool:
        return self.current_temp >= self.throttle_c

    @property
    def severity(self) -> str:
        """Human-readable thermal severity level."""
        if self.current_temp < 50:
            return "COOL"
        if self.current_temp < self.throttle_c:
            return "WARM"
        if self.current_temp < self.max_safe_c:
            return "THROTTLING"
        return "CRITICAL"


class ThermalManager:
    """
    Multi-zone thermal manager.

    Responsible for:
    1. Updating all thermal zones each tick.
    2. Emitting thermal events (throttle start/stop, critical).
    3. Advising the scheduler on safe power envelope.
    """

    def __init__(self):
        self.zones: List[ThermalZone] = [
            ThermalZone("CPU_BIG",    throttle_c=75, max_safe_c=85),
            ThermalZone("CPU_LITTLE", throttle_c=70, max_safe_c=80),
            ThermalZone("SOC_PKG",    throttle_c=80, max_safe_c=90),
        ]
        self.events: List[dict] = []
        self._prev_states: dict = {}

    def update_all(self, powers_mw: List[float], dt_ms: float = 10.0):
        """Update every zone. powers_mw must match len(self.zones)."""
        for zone, power in zip(self.zones, powers_mw):
            prev_state = self._prev_states.get(zone.name, "COOL")
            zone.step(power, dt_ms)
            new_state = zone.severity
            if new_state != prev_state:
                self.events.append({
                    "time_ms": dt_ms,
                    "zone": zone.name,
                    "from": prev_state,
                    "to": new_state,
                    "temp_c": zone.current_temp,
                })
            self._prev_states[zone.name] = new_state

    @property
    def hottest_zone(self) -> ThermalZone:
        return max(self.zones, key=lambda z: z.current_temp)

    @property
    def global_headroom(self) -> float:
        """Minimum headroom across all zones (most conservative)."""
        return min(z.headroom for z in self.zones)

    def power_budget_mw(self, base_budget: float = 500.0) -> float:
        """
        Compute allowed power budget based on thermal state.
        Derate linearly when headroom < 0.5.
        """
        hr = self.global_headroom
        if hr >= 0.5:
            return base_budget
        return base_budget * (hr / 0.5)

    def migration_advice(self) -> Optional[str]:
        """
        Return migration advice if BIG cluster is too hot.
        Returns 'migrate_to_little', 'throttle', or None.
        """
        big = next((z for z in self.zones if z.name == "CPU_BIG"), None)
        little = next((z for z in self.zones if z.name == "CPU_LITTLE"), None)
        if big and big.is_throttling and little and not little.is_throttling:
            return "migrate_to_little"
        if any(z.is_critical for z in self.zones):
            return "throttle"
        return None

    def snapshot(self) -> List[dict]:
        return [
            {
                "zone": z.name,
                "temp_c": z.current_temp,
                "severity": z.severity,
                "headroom": round(z.headroom, 3),
            }
            for z in self.zones
        ]
