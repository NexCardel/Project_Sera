"""The six day profiles of the stress report (section 5J) through the simulation: zero missed, zero mis-attributed.
Full size, on a virtual clock (no real sleeps). The same profiles with real sleeps are the owner check of WP6."""
import os

import pytest

from tests.sgt_sim import profiles
from tests.sgt_sim.harness import assert_profile, sim  # noqa: F401 - sim is a fixture


def _run(p, sim):
    h = profiles.play(p, sim)
    assert_profile(h, p.expected)
    return h


def test_profile_1_zero_missed(sim):
    _run(profiles.b2b_marathon(), sim)


def test_profile_2_zero_missed(sim):
    _run(profiles.due_date_rush(), sim)


def test_profile_3_zero_missed(sim):
    _run(profiles.parallel_filing(), sim)


def test_profile_3_background_success_is_caught(sim):
    h = _run(profiles.parallel_filing(background_success=True), sim)
    assert h.counts["bg"] > 0                       # the windows not in front were watched


def test_profile_4_zero_missed(sim):
    _run(profiles.itr_season(), sim)


def test_profile_5_zero_missed(sim):
    _run(profiles.all_day_pc(), sim)


def test_profile_6_zero_missed(sim):
    _run(profiles.interrupted_day(), sim)


real = pytest.mark.skipif(not os.environ.get("SGT_SIM_REAL"), reason="real-time run: set SGT_SIM_REAL=1 (about 20 min)")


@real
def test_profile_1_real_sleeps(sim):
    p = profiles.b2b_marathon(minutes=10.0)
    assert_profile(profiles.play(p, sim, real=True), p.expected)


@real
def test_profile_2_real_sleeps(sim):
    p = profiles.due_date_rush(clients=8)
    assert_profile(profiles.play(p, sim, real=True), p.expected)
