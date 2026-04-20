"""
scheduler_core.py
Core CPU Scheduling Engine with DVFS and Thermal-Aware Scheduling
"""

import time
import math
import random
from dataclasses import dataclass, field
from typing import List, Optional
from enum import Enum


class TaskPriority(Enum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class TaskState(Enum):
    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"


@dataclass
class Task:
    task_id: int
    name: str
    burst_time: float          # CPU time needed (ms)
    arrival_time: float        # When task arrives (ms)
    priority: TaskPriority
    deadline: Optional[float] = None   # Deadline in ms (EDF)
    remaining_time: float = 0.0
    start_time: Optional[float] = None
    finish_time: Optional[float] = None
    state: TaskState = TaskState.READY
    cpu_intensity: float = 0.5  # 0.0 (light) to 1.0 (heavy)
    memory_footprint: float = 0.0  # MB

    def __post_init__(self):
        self.remaining_time = self.burst_time

    @property
    def turnaround_time(self) -> Optional[float]:
        if self.finish_time is not None:
            return self.finish_time - self.arrival_time
        return None

    @property
    def waiting_time(self) -> Optional[float]:
        if self.turnaround_time is not None:
            return self.turnaround_time - self.burst_time
        return None

    @property
    def is_completed(self) -> bool:
        return self.state == TaskState.COMPLETED


@dataclass
class DVFSLevel:
    frequency_mhz: float
    voltage_v: float
    power_factor: float   # relative power consumption

    @property
    def dynamic_power(self) -> float:
        # P_dynamic ∝ C * V^2 * f
        return self.power_factor * (self.voltage_v ** 2) * (self.frequency_mhz / 1000)

    @property
    def label(self) -> str:
        return f"{self.frequency_mhz:.0f} MHz / {self.voltage_v:.2f}V"


# Predefined DVFS operating points (typical ARM Cortex-A profile)
DVFS_LEVELS = [
    DVFSLevel(frequency_mhz=300,  voltage_v=0.80, power_factor=0.10),
    DVFSLevel(frequency_mhz=600,  voltage_v=0.90, power_factor=0.25),
    DVFSLevel(frequency_mhz=900,  voltage_v=1.00, power_factor=0.45),
    DVFSLevel(frequency_mhz=1200, voltage_v=1.10, power_factor=0.65),
    DVFSLevel(frequency_mhz=1500, voltage_v=1.20, power_factor=0.82),
    DVFSLevel(frequency_mhz=1800, voltage_v=1.30, power_factor=1.00),
]


class DVFSController:
    """Dynamic Voltage and Frequency Scaling Controller"""

    def __init__(self):
        self.current_level_idx: int = 2
        self.history: List[dict] = []
        self.total_energy: float = 0.0

    @property
    def current_level(self) -> DVFSLevel:
        return DVFS_LEVELS[self.current_level_idx]

    def select_frequency(self, queue_length: int, avg_intensity: float,
                          thermal_headroom: float, deadline_pressure: float) -> DVFSLevel:
        """
        Intelligently select DVFS level based on workload and thermal state.
        Returns the chosen DVFSLevel.
        """
        # Workload score 0..1
        workload_score = min(1.0, (queue_length / 10.0) * 0.4 +
                             avg_intensity * 0.4 +
                             deadline_pressure * 0.2)

        # Thermal throttle: reduce score if temp is high
        thermal_factor = max(0.0, thermal_headroom)  # 0=hot, 1=cool
        adjusted_score = workload_score * thermal_factor

        # Map to DVFS level
        idx = min(len(DVFS_LEVELS) - 1,
                  int(adjusted_score * len(DVFS_LEVELS)))
        self.current_level_idx = idx

        level = DVFS_LEVELS[idx]
        self.history.append({
            "timestamp": time.time(),
            "level": level.label,
            "workload_score": round(workload_score, 3),
            "adjusted_score": round(adjusted_score, 3),
        })
        return level

    def record_energy(self, duration_ms: float):
        """Accumulate energy consumption for current level (in mJ)."""
        power_mw = self.current_level.dynamic_power * 100  # scale to mW
        energy_mj = power_mw * (duration_ms / 1000.0)
        self.total_energy += energy_mj
        return energy_mj


class ThermalModel:
    """Simple thermal model for the CPU package."""

    AMBIENT_TEMP = 25.0       # °C
    MAX_SAFE_TEMP = 85.0      # °C
    THROTTLE_TEMP = 75.0      # °C
    THERMAL_RESISTANCE = 0.3  # °C / mW (simplified)
    COOLING_RATE = 0.05       # °C per simulation tick when idle

    def __init__(self):
        self.temperature: float = self.AMBIENT_TEMP
        self.history: List[float] = [self.AMBIENT_TEMP]

    def update(self, power_mw: float, is_idle: bool = False):
        if is_idle:
            # Cool down toward ambient
            self.temperature -= self.COOLING_RATE * (self.temperature - self.AMBIENT_TEMP)
        else:
            delta = self.THERMAL_RESISTANCE * power_mw * 0.01
            self.temperature = min(self.MAX_SAFE_TEMP,
                                   self.temperature + delta)
        self.history.append(round(self.temperature, 2))

    @property
    def headroom(self) -> float:
        """Fraction of safe headroom remaining (1=cool, 0=at throttle limit)."""
        span = self.THROTTLE_TEMP - self.AMBIENT_TEMP
        return max(0.0, (self.THROTTLE_TEMP - self.temperature) / span)

    @property
    def is_throttling(self) -> bool:
        return self.temperature >= self.THROTTLE_TEMP


class EnergyAwareScheduler:
    """
    Main scheduler combining:
    - Earliest Deadline First (EDF) with priority fallback
    - DVFS control
    - Thermal-aware task migration / throttling
    """

    def __init__(self, time_quantum_ms: float = 10.0):
        self.time_quantum = time_quantum_ms
        self.dvfs = DVFSController()
        self.thermal = ThermalModel()
        self.ready_queue: List[Task] = []
        self.completed_tasks: List[Task] = []
        self.current_time: float = 0.0
        self.log: List[dict] = []
        self.tick_data: List[dict] = []

    def add_task(self, task: Task):
        self.ready_queue.append(task)

    def _select_next_task(self) -> Optional[Task]:
        """EDF with priority tiebreak."""
        eligible = [t for t in self.ready_queue
                    if t.arrival_time <= self.current_time
                    and t.state == TaskState.READY]
        if not eligible:
            return None

        # Sort: tasks with deadline first (EDF), then by priority
        def sort_key(t: Task):
            dl = t.deadline if t.deadline is not None else float('inf')
            return (dl, -t.priority.value)

        eligible.sort(key=sort_key)
        return eligible[0]

    def _compute_deadline_pressure(self) -> float:
        """How urgent are the deadlines in the queue? 0=relaxed, 1=critical."""
        now = self.current_time
        pressures = []
        for t in self.ready_queue:
            if t.deadline and t.deadline > now:
                slack = t.deadline - now
                pressures.append(max(0.0, 1.0 - slack / 200.0))
        return max(pressures) if pressures else 0.0

    def run(self, max_ticks: int = 200) -> dict:
        """Simulate the scheduler and return statistics."""
        tick = 0
        while tick < max_ticks:
            tick += 1
            # --- Arrive new tasks ---
            for t in self.ready_queue:
                if t.arrival_time <= self.current_time and t.state == TaskState.WAITING:
                    t.state = TaskState.READY

            active = [t for t in self.ready_queue if not t.is_completed]
            if not active:
                break

            # --- DVFS decision ---
            avg_intensity = (sum(t.cpu_intensity for t in active) / len(active)
                             if active else 0.0)
            dl_pressure = self._compute_deadline_pressure()
            level = self.dvfs.select_frequency(
                queue_length=len(active),
                avg_intensity=avg_intensity,
                thermal_headroom=self.thermal.headroom,
                deadline_pressure=dl_pressure,
            )

            # --- Pick task ---
            task = self._select_next_task()
            if task is None:
                # CPU idle — cool down
                self.thermal.update(power_mw=0, is_idle=True)
                self.current_time += self.time_quantum
                self.tick_data.append(self._snapshot(None, 0))
                continue

            # --- Execute for one quantum ---
            if task.start_time is None:
                task.start_time = self.current_time
            task.state = TaskState.RUNNING

            # Speed-up from higher frequency (linear model)
            speed_ratio = level.frequency_mhz / DVFS_LEVELS[-1].frequency_mhz
            effective_work = self.time_quantum * speed_ratio
            task.remaining_time = max(0.0, task.remaining_time - effective_work)

            power_mw = level.dynamic_power * 100 * task.cpu_intensity
            self.thermal.update(power_mw=power_mw)
            energy = self.dvfs.record_energy(self.time_quantum)

            self.current_time += self.time_quantum

            if task.remaining_time <= 0:
                task.state = TaskState.COMPLETED
                task.finish_time = self.current_time
                self.completed_tasks.append(task)
                self.ready_queue.remove(task)
                self.log.append({
                    "task_id": task.task_id,
                    "name": task.name,
                    "finish_time": self.current_time,
                    "turnaround": task.turnaround_time,
                    "waiting": task.waiting_time,
                    "energy_mj": round(energy, 4),
                })
            else:
                task.state = TaskState.READY

            self.tick_data.append(self._snapshot(task, energy))

        return self._compute_stats()

    def _snapshot(self, task: Optional[Task], energy: float) -> dict:
        return {
            "time": round(self.current_time, 1),
            "task": task.name if task else "IDLE",
            "temp": round(self.thermal.temperature, 2),
            "freq_mhz": self.dvfs.current_level.frequency_mhz,
            "voltage": self.dvfs.current_level.voltage_v,
            "energy_mj": round(energy, 4),
            "queue_len": len(self.ready_queue),
        }

    def _compute_stats(self) -> dict:
        if not self.completed_tasks:
            return {}
        tats = [t.turnaround_time for t in self.completed_tasks if t.turnaround_time]
        wts  = [t.waiting_time    for t in self.completed_tasks if t.waiting_time is not None]
        return {
            "total_tasks": len(self.completed_tasks),
            "avg_turnaround_ms": round(sum(tats) / len(tats), 2) if tats else 0,
            "avg_waiting_ms":    round(sum(wts)  / len(wts),  2) if wts  else 0,
            "total_energy_mj":   round(self.dvfs.total_energy, 4),
            "max_temp_c":        round(max(self.thermal.history), 2),
            "avg_temp_c":        round(sum(self.thermal.history) / len(self.thermal.history), 2),
            "throughput":        round(len(self.completed_tasks) / (self.current_time / 1000), 4),
            "tick_data":         self.tick_data,
            "log":               self.log,
        }
