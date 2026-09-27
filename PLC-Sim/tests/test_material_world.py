import math
import pytest

from material_world import ModelError, World


def test_solute_and_total_mass_are_different_and_conserved():
    w = World()
    w.add_sample("stock", volume_ul=1000, concentration_mg_l=100, density_g_ml=1.2)
    dst = w.add_sample("tube", volume_ul=1000, capacity_ul=3000, tare_g=5)
    before = w.totals()
    w.transfer("stock", "tube", 500)
    assert dst.liquid_mass_g == pytest.approx(1.6)
    assert dst.solute_ug == pytest.approx(50)
    assert dst.gross_mass_g == pytest.approx(6.6)
    assert w.totals() == pytest.approx(before)


def test_mixing_changes_local_concentration_preserves_total_and_partial_transfer():
    w = World()
    w.add_sample("stock", volume_ul=1000, concentration_mg_l=100)
    dst = w.add_sample("well", volume_ul=100, capacity_ul=300)
    w.transfer("stock", "well", 100)
    assert dst.concentration_mg_l == pytest.approx(50)
    assert dst.sample_concentration_mg_l == 0
    before = w.totals()
    dst.mix(10)
    assert dst.homogeneity > .999
    assert dst.sample_concentration_mg_l == pytest.approx(50, abs=.01)
    w.add_sample("tip", capacity_ul=100)
    w.transfer("well", "tip", 50)
    assert w.sample("tip").concentration_mg_l == pytest.approx(50, abs=.01)
    assert w.totals() == pytest.approx(before)


@pytest.mark.parametrize("volume", [-1, float("nan"), float("inf"), True, 101])
def test_invalid_transfer_has_no_effect(volume):
    w = World(); w.add_sample("a", volume_ul=100); w.add_sample("b", capacity_ul=100)
    before = w.snapshot()
    with pytest.raises(ModelError):
        w.transfer("a", "b", volume)
    assert w.snapshot() == before


def test_callback_receives_bounded_steps_and_clock_is_reproducible():
    w = World(); steps = []; w.register_tick(steps.append); w.advance(.35)
    assert sum(steps) == pytest.approx(.35)
    assert max(steps) <= .1
    assert w.time_s == .35
    with pytest.raises(ModelError): w.advance(float("nan"))


def test_capacity_reject_does_not_withdraw_source():
    w = World(); w.add_sample("a", volume_ul=200); w.add_sample("b", capacity_ul=50)
    before = w.totals()
    with pytest.raises(ModelError): w.transfer("a", "b", 100)
    assert w.sample("a").volume_ul == 200
    assert w.totals() == before
