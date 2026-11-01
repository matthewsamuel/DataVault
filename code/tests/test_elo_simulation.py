"""
The simulation behind docs/ELO.md (tests/elo_simulation.py): seeded, so exactly repeatable,
and it proves that the new rating system (Glicko-2 with information-driven pairs) reaches
the accuracy of the old Elo system with clearly fewer comparisons.
"""
import random

import pytest

import elo_simulation as sim


def test_the_metrics_are_right():
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert sim.spearman(x, x) == pytest.approx(1.0) and sim.kendall(x, x) == pytest.approx(1.0)
    assert sim.spearman(x, x[::-1]) == pytest.approx(-1.0) and sim.kendall(x, x[::-1]) == pytest.approx(-1.0)
    truth = list(range(20))
    estimate = truth[:]
    estimate[19], estimate[0] = estimate[0], estimate[19]        # the best is ranked last
    assert sim.top_precision(truth, estimate) == pytest.approx(0.9)
    rows = [{"comparisons": 10, "m": 0.2}, {"comparisons": 20, "m": 0.6}]
    assert sim.comparisons_to_reach(rows, "m", 0.4) == 15 and sim.comparisons_to_reach(rows, "m", 0.7) is None


def test_the_simulated_user_is_noisy_but_mostly_right():
    user = sim.User(2, random.Random(1), lapse=0.05)
    user.truth = [200.0, 0.0]                  # 0.76 by Bradley-Terry, minus lapses
    picks = [user.choose(0, 1) for _ in range(4000)]
    assert 0.70 < picks.count("a") / 4000 < 0.80
    user = sim.User(2, random.Random(2), lapse=0.0, draw_margin=50)
    user.truth = [10.0, 0.0]
    assert "draw" in {user.choose(0, 1) for _ in range(50)}


def test_a_run_is_exactly_repeatable():
    for cls in (sim.LegacySystem, sim.CurrentSystem):
        first = sim.run(cls, 30, seed=3, checkpoints=[30, 90])
        assert first == sim.run(cls, 30, seed=3, checkpoints=[30, 90])
        assert first[-1]["comparisons"] == 90


def test_the_new_system_needs_clearly_fewer_comparisons():
    """At the accuracy the old system reaches with its full budget (15 comparisons per
    photo), the new one needs at most 80% of the comparisons for the whole ranking with a
    decisive user and 90% with a hesitant one, and never more for the top 10 (with 60 photos
    the top-10 curve of a few seeds is noisy). The full table (100 photos, 20 seeds) is in
    docs/ELO.md."""
    for scenario, share in (("beslist", 0.8), ("standaard", 0.9)):
        _, spread, lapse = sim.SCENARIOS[scenario]
        result = sim.savings(60, 8, 15, spread, lapse)
        for metric, level, old, new in result["rows"]:
            limit = share if metric != "top10" else 1.0
            assert old and new and new <= limit * old, (scenario, metric, level, old, new)


def test_settled_grows_with_the_comparisons():
    rows = sim.run(sim.CurrentSystem, 40, seed=5, checkpoints=[20, 100, 300, 600])
    settled = [r["settled"] for r in rows]
    assert settled == sorted(settled) and settled[0] < 0.3 and settled[-1] > 0.9
