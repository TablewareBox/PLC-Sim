"""Sensor output conditioning; consumes ray observations, never object identities."""
from __future__ import annotations
from dataclasses import dataclass
import math


@dataclass
class PhotoelectricSignal:
    debounce_s: float = 0.05
    value: bool | None = None
    candidate: bool | None = None
    candidate_since: float | None = None
    last_time: float | None = None

    def sample(self, time_s, raw_detected, *, valid=True, enabled=True):
        t = float(time_s)
        if not math.isfinite(t):
            raise ValueError("sample time must be finite")
        reset = self.last_time is not None and t < self.last_time
        self.last_time = t
        if reset or not valid or not enabled:
            self.value = self.candidate = self.candidate_since = None
            return {"value": None, "quality": "unknown", "reason":
                    "disabled" if not enabled else "time_reset" if reset else "invalid_reading"}
        if type(raw_detected) is not bool:
            raise ValueError("valid observations require a boolean")
        if self.candidate is not raw_detected:
            self.candidate, self.candidate_since = raw_detected, t
        if t - self.candidate_since + 1e-9 >= self.debounce_s:
            self.value = self.candidate
        return {"value": self.value,
                "quality": "good" if self.value is not None else "unknown",
                "reason": "settled" if self.value is self.candidate else "debouncing"}
