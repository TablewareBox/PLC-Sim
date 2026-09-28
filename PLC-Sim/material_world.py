"""旧仪器过程共用的物料容器与显式推进宿主，不创建墙钟或后台线程。

一个运行组合注入同一实例；advance 仅消费调用方给出的时间增量。
尚未接入统一扫描屏障、epoch 或 Isaac 世界；不能单独授予联合时序资格。
"""
from __future__ import annotations

import math
import random
import threading
from typing import Callable

if __package__:
    from .material_state import ModelError, Sample, Liquid, finite
else:
    from material_state import ModelError, Sample, Liquid, finite


class World:
    def __init__(self, seed=1):
        self.seed = seed
        self.rng = random.Random(seed)
        self.time_s = 0.0
        self.samples: dict[str, Sample] = {}
        self.plates: dict[str, dict[str, str]] = {}
        self.plate_locations: dict[str, str] = {}
        self.events: list[dict] = []
        self.lock = threading.RLock()
        self._tick: list[Callable[[float], None]] = []
        self._operation_geometry_guard = None

    def emit(self, kind, **fields):
        with self.lock:
            event = {"sequence": len(self.events) + 1, "time_s": self.time_s, "kind": kind, **fields}
            self.events.append(event)
            return event

    def add_sample(self, id, capacity_ul=100000, volume_ul=0, concentration_mg_l=0,
                   tare_g=0, density_g_ml=1, temperature_c=25):
        with self.lock:
            if not isinstance(id, str) or not id or id in self.samples:
                raise ModelError("invalid_or_duplicate_sample")
            capacity = finite(capacity_ul, "capacity", 1e-12)
            volume = finite(volume_ul, "volume", 0, capacity)
            concentration = finite(concentration_mg_l, "concentration", 0)
            tare = finite(tare_g, "tare", 0)
            density = finite(density_g_ml, "density", 1e-12)
            temp = finite(temperature_c, "temperature", -273.15)
            sample = Sample(id, capacity, tare, temp,
                            Liquid(volume, concentration * volume / 1000.0, volume * density / 1000.0),
                            lineage={id} if volume else set())
            self.samples[id] = sample
            self.emit("sample.created", sample_id=id, volume_ul=volume, concentration_mg_l=concentration)
            return sample

    def add_plate(self, id, rows=8, columns=12, capacity_ul=300):
        with self.lock:
            if not isinstance(id, str) or not id or id in self.plates:
                raise ModelError("invalid_or_duplicate_plate")
            if isinstance(rows, bool) or isinstance(columns, bool) or not isinstance(rows, int) or not isinstance(columns, int) or not (1 <= rows <= 26 and 1 <= columns <= 48):
                raise ModelError("invalid_plate_shape")
            capacity_ul = finite(capacity_ul, "capacity", 1e-12)
            mapping = {f"{chr(65 + row)}{col + 1}": f"{id}:{chr(65 + row)}{col + 1}"
                       for row in range(rows) for col in range(columns)}
            if set(mapping.values()) & set(self.samples):
                raise ModelError("duplicate_well_sample")
            for sample_id in mapping.values():
                self.add_sample(sample_id, capacity_ul=capacity_ul).container_kind = "well"
            self.plates[id] = mapping
            self.plate_locations[id] = "deck"
            self.emit("plate.created", plate_id=id, rows=rows, columns=columns)
            return mapping

    def check_operation_geometry(self, boundary, **details):
        # Optional private admission hook. The World remains custody authority.
        if self._operation_geometry_guard is not None:
            self._operation_geometry_guard(boundary, details)

    def claim_plate(self, plate_id, device_id):
        with self.lock:
            if plate_id not in self.plates:
                raise ModelError("unknown_plate")
            if self.plate_locations.get(plate_id) != "deck":
                raise ModelError("plate_in_other_device")
            details = dict(plate_id=plate_id, source="deck", target=device_id, device_id=device_id)
            self.check_operation_geometry("handoff", **details)
            self.plate_locations[plate_id] = device_id
            event = self.emit("plate.claimed", plate_id=plate_id, device_id=device_id)
            self.check_operation_geometry("handoff_committed", model_event_sequence=event["sequence"], **details)

    def release_plate(self, plate_id, device_id):
        with self.lock:
            if self.plate_locations.get(plate_id) != device_id:
                raise ModelError("plate_location_mismatch")
            details = dict(plate_id=plate_id, source=device_id, target="deck", device_id=device_id)
            self.check_operation_geometry("handoff", **details)
            self.plate_locations[plate_id] = "deck"
            event = self.emit("plate.released", plate_id=plate_id, device_id=device_id)
            self.check_operation_geometry("handoff_committed", model_event_sequence=event["sequence"], **details)

    def sample(self, id):
        try:
            return self.samples[id]
        except (KeyError, TypeError):
            raise ModelError("unknown_sample") from None

    def transfer(self, source_id, target_id, volume_ul):
        with self.lock:
            volume = finite(volume_ul, "volume", 0)
            source, target = self.sample(source_id), self.sample(target_id)
            if source_id == target_id:
                raise ModelError("same_source_target")
            if volume > source.volume_ul + 1e-8:
                raise ModelError("insufficient_volume")
            if volume > target.free_ul + 1e-8:
                raise ModelError("capacity_exceeded")
            volume = min(volume, source.volume_ul, target.free_ul)
            packet = source._withdraw(volume)
            target._receive(packet, source.temperature_c, source.lineage)
            self.emit("material.transferred", source_id=source_id, target_id=target_id,
                      volume_ul=packet.volume_ul, solute_ug=packet.solute_ug, liquid_mass_g=packet.mass_g)
            return packet.volume_ul

    def register_tick(self, callback):
        self._tick.append(callback)

    def advance(self, dt_s):
        dt_s = finite(dt_s, "dt", 0, 86400)
        with self.lock:
            end = self.time_s + dt_s
            while self.time_s < end - 1e-10:
                step = min(0.1, end - self.time_s)
                self.time_s += step
                for callback in tuple(self._tick):
                    callback(step)
            self.time_s = end

    def totals(self):
        with self.lock:
            return {"volume_ul": math.fsum(s.volume_ul for s in self.samples.values()),
                    "solute_ug": math.fsum(s.solute_ug for s in self.samples.values()),
                    "liquid_mass_g": math.fsum(s.liquid_mass_g for s in self.samples.values())}

    def snapshot(self):
        with self.lock:
            return {"seed": self.seed, "time_s": self.time_s, "totals": self.totals(),
                    "plates": {k: dict(v) for k, v in self.plates.items()},
                    "plate_locations": dict(self.plate_locations),
                    "samples": {k: {"id": s.id, "capacity_ul": s.capacity_ul, "volume_ul": s.volume_ul,
                                    "solute_ug": s.solute_ug, "liquid_mass_g": s.liquid_mass_g,
                                    "tare_g": s.tare_g, "temperature_c": s.temperature_c,
                                    "container_kind": s.container_kind,
                                    "concentration_mg_l": s.concentration_mg_l,
                                    "sample_concentration_mg_l": s.sample_concentration_mg_l,
                                    "homogeneity": s.homogeneity, "lineage": sorted(s.lineage)}
                                for k, s in self.samples.items()}}
