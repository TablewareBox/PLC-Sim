"""独立检查物料守恒、局部浓度和部分效果；无设备/世界依赖。"""
import math

import pytest

from material_state import Liquid, ModelError, Sample, finite


@pytest.mark.parametrize("value", [True, False, float("nan"), float("inf"), -float("inf"), "bad"])
def test_finite_rejects_non_numeric_or_nonfinite(value):
    with pytest.raises(ModelError):
        finite(value, "quantity")


@pytest.mark.parametrize("value", [-1, 11])
def test_finite_rejects_outside_declared_range(value):
    with pytest.raises(ModelError):
        finite(value, "quantity", minimum=0, maximum=10)


def test_partial_liquid_withdrawal_conserves_three_extensive_quantities():
    liquid = Liquid(1000, 120, 1.2)
    portion = liquid.take(250)
    assert (portion.volume_ul, portion.solute_ug, portion.mass_g) == pytest.approx((250, 30, .3))
    assert (liquid.volume_ul, liquid.solute_ug, liquid.mass_g) == pytest.approx((750, 90, .9))
    liquid.add(portion)
    assert (liquid.volume_ul, liquid.solute_ug, liquid.mass_g) == pytest.approx((1000, 120, 1.2))


def test_mix_changes_local_measurement_but_preserves_totals():
    sample = Sample("cup", 300, tare_g=5, lower=Liquid(100, 0, .1), upper=Liquid(100, 10, .12))
    assert sample.concentration_mg_l == 50
    assert sample.sample_concentration_mg_l == 0
    sample.mix(2, rate=.5)
    assert sample.sample_concentration_mg_l == pytest.approx(50 * (1-math.exp(-1)))
    assert (sample.volume_ul, sample.solute_ug, sample.liquid_mass_g, sample.gross_mass_g) == pytest.approx((200, 10, .22, 5.22))


def test_receive_tracks_temperature_lineage_and_partial_effects():
    sample = Sample("cup", 500, temperature_c=20, lower=Liquid(100, 2, .1), lineage={"water"})
    sample._receive(Liquid(100, 10, .12), 40, {"stock"})
    assert sample.temperature_c == 30
    assert sample.lineage == {"water", "stock"}
    taken = sample._withdraw(150)
    assert (taken.volume_ul, taken.solute_ug, taken.mass_g) == pytest.approx((150, 7, .16))
    assert (sample.volume_ul, sample.solute_ug, sample.liquid_mass_g) == pytest.approx((50, 5, .06))


def test_instances_do_not_share_mutable_material_or_lineage():
    first, second = Sample("a", 100), Sample("b", 100)
    first._receive(Liquid(20, 1, .02), 25, {"stock"})
    assert second.volume_ul == 0
    assert second.lineage == set()
