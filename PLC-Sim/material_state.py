"""有限液体状态和两层混匀模型；不创建世界、时钟或设备端点。

单位为 uL、ug、g、摄氏度；混匀为合成过程，不是标定后的流体模型。
构造/转移的容量、非负性与并发检查由调用方负责，详见迁移说明。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


class ModelError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def finite(value, name: str, minimum=None, maximum=None) -> float:
    if isinstance(value, bool):
        raise ModelError(f"invalid_{name}")
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        raise ModelError(f"invalid_{name}") from None
    if not math.isfinite(result):
        raise ModelError(f"invalid_{name}")
    if minimum is not None and result < minimum:
        raise ModelError(f"invalid_{name}")
    if maximum is not None and result > maximum:
        raise ModelError(f"invalid_{name}")
    return result


@dataclass
class Liquid:
    volume_ul: float = 0.0
    solute_ug: float = 0.0
    mass_g: float = 0.0

    def add(self, other: "Liquid") -> None:
        self.volume_ul += other.volume_ul
        self.solute_ug += other.solute_ug
        self.mass_g += other.mass_g

    def take(self, volume_ul: float) -> "Liquid":
        if volume_ul <= 0:
            return Liquid()
        fraction = min(1.0, volume_ul / self.volume_ul)
        taken = Liquid(self.volume_ul * fraction, self.solute_ug * fraction, self.mass_g * fraction)
        self.volume_ul -= taken.volume_ul
        self.solute_ug -= taken.solute_ug
        self.mass_g -= taken.mass_g
        return taken


@dataclass
class Sample:
    id: str
    capacity_ul: float
    tare_g: float = 0.0
    temperature_c: float = 25.0
    lower: Liquid = field(default_factory=Liquid)
    upper: Liquid = field(default_factory=Liquid)
    lineage: set[str] = field(default_factory=set)
    container_kind: str = "container"

    @property
    def volume_ul(self):
        return self.lower.volume_ul + self.upper.volume_ul

    @property
    def solute_ug(self):
        return self.lower.solute_ug + self.upper.solute_ug

    @property
    def liquid_mass_g(self):
        return self.lower.mass_g + self.upper.mass_g

    @property
    def gross_mass_g(self):
        return self.tare_g + self.liquid_mass_g

    @property
    def free_ul(self):
        return max(0.0, self.capacity_ul - self.volume_ul)

    @property
    def concentration_mg_l(self):
        return 1000.0 * self.solute_ug / self.volume_ul if self.volume_ul > 1e-12 else 0.0

    @property
    def sample_concentration_mg_l(self):
        layer = self.lower if self.lower.volume_ul > 1e-12 else self.upper
        return 1000.0 * layer.solute_ug / layer.volume_ul if layer.volume_ul > 1e-12 else 0.0

    @property
    def homogeneity(self):
        a, b = self.lower, self.upper
        if min(a.volume_ul, b.volume_ul) < 1e-12:
            return 1.0
        ca, cb = a.solute_ug / a.volume_ul, b.solute_ug / b.volume_ul
        return max(0.0, 1.0 - abs(ca - cb) / max(ca, cb, 1e-12))

    def _receive(self, liquid: Liquid, temperature_c: float, lineage: set[str]):
        before = self.volume_ul
        if liquid.volume_ul > 0:
            self.temperature_c = (self.temperature_c * before + temperature_c * liquid.volume_ul) / (before + liquid.volume_ul)
        (self.lower if before < 1e-12 else self.upper).add(liquid)
        self.lineage.update(lineage)

    def _withdraw(self, volume_ul: float) -> Liquid:
        result = Liquid()
        for layer in (self.lower, self.upper):
            remaining = max(0.0, volume_ul - result.volume_ul)
            if layer.volume_ul > 1e-12 and remaining > 1e-12:
                result.add(layer.take(min(remaining, layer.volume_ul)))
        if self.lower.volume_ul < 1e-12:
            self.lower, self.upper = self.upper, Liquid()
        return result

    def mix(self, dt_s: float, rate=1.0):
        dt_s = finite(dt_s, "mix_dt", 0)
        rate = finite(rate, "mix_rate", 0)
        a, b = self.lower, self.upper
        if min(a.volume_ul, b.volume_ul) < 1e-12:
            return
        total_v = self.volume_ul
        decay = math.exp(-rate * dt_s)
        for attr in ("solute_ug", "mass_g"):
            total = getattr(a, attr) + getattr(b, attr)
            difference = (getattr(a, attr) / a.volume_ul - getattr(b, attr) / b.volume_ul) * decay
            a_value = a.volume_ul * (total / total_v + difference * b.volume_ul / total_v)
            setattr(a, attr, a_value)
            setattr(b, attr, total - a_value)
