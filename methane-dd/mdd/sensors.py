"""Sensor models for the due-diligence tool (SPEC section 6a): detection probability and quantification error.

The functional form is MRVSim's logistic POD in ln(Q / u-adjusted) [see mrvsim/sensors/pod.py]; the parameters
live in ``config/sensors.yaml`` with their citation keys.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import numpy as np
import yaml
from scipy.special import expit

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "sensors.yaml"
_LN9 = float(np.log(9.0))


@dataclass(frozen=True)
class SensorModel:
    key: str
    name: str
    platform: str
    pod50_kg_h: float
    pod90_kg_h: float
    gamma: float
    u_ref_m_s: float
    ratio_95: tuple[float, float]     # 95 % range of reported / true rate
    provenance: str
    u_min_m_s: float = 2.0

    @property
    def a(self) -> float:
        return -self.b * float(np.log(self.pod50_kg_h))

    @property
    def b(self) -> float:
        return _LN9 / (float(np.log(self.pod90_kg_h)) - float(np.log(self.pod50_kg_h)))

    def pod(self, q_kg_h: np.ndarray | float, wind_m_s: np.ndarray | float | None = None) -> np.ndarray:
        """P(detect | rate Q, wind u): logistic in ln(Q (u_ref / max(u, u_min))^gamma)."""
        q = np.asarray(q_kg_h, dtype=float)
        f = np.ones_like(q)
        if wind_m_s is not None and self.gamma != 0.0:
            u = np.maximum(np.asarray(wind_m_s, dtype=float), self.u_min_m_s)
            f = (self.u_ref_m_s / u) ** self.gamma
        qe = q * f
        with np.errstate(divide="ignore"):
            p = expit(self.a + self.b * np.log(np.where(qe > 0, qe, 1.0)))
        return np.where(qe > 0, p, 0.0)

    @property
    def log_sigma(self) -> float:
        """Log-sd of the quantification error implied by the 95 % ratio range (symmetric in log space)."""
        lo, hi = self.ratio_95
        return float((np.log(hi) - np.log(lo)) / (2 * 1.96))

    def detection_range_kg_h(self) -> tuple[float, float]:
        """Rates a detected source plausibly had: from POD10 up to 100 x POD90 (used for the zero-detection draw)."""
        pod10 = float(np.exp(-(self.a + np.log(9.0)) / self.b))
        return pod10, 100.0 * self.pod90_kg_h


@dataclass(frozen=True)
class SensorLibrary:
    sensors: Mapping[str, SensorModel]
    default_wind_m_s: float
    clear_sky_max_cloud_pct: float

    def __getitem__(self, key: str) -> SensorModel:
        return self.sensors[key]

    def __contains__(self, key: str) -> bool:
        return key in self.sensors


def load_sensors(path: str | Path = CONFIG_PATH) -> SensorLibrary:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    sensors = {}
    for key, s in raw["sensors"].items():
        sensors[key] = SensorModel(key, s["name"], s["platform"], float(s["pod50_kg_h"]), float(s["pod90_kg_h"]), float(s.get("gamma", 1.0)),
                                   float(s.get("u_ref_m_s", 3.0)), tuple(float(x) for x in s["quantification_ratio_95"]), str(s.get("provenance", "verify")))
    return SensorLibrary(sensors, float(raw.get("default_wind_m_s", 3.0)), float(raw.get("clear_sky_max_cloud_pct", 30)))
