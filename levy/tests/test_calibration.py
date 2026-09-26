"""test_calibration: isotonic fit is monotonic and bounded in [0.01, 0.99]."""

from __future__ import annotations

import random

from levy.core.calibrate import (
    CLAMP_HI,
    CLAMP_LO,
    fit_calibration,
    fit_isotonic,
    fit_platt,
    neutral_map,
)


def test_bounds_on_all_methods():
    for model in (
        neutral_map(),
        fit_platt([0.1, 0.9], [0, 1]),
        fit_isotonic([i / 20 for i in range(21)], [0] * 10 + [1] * 11),
    ):
        for x in [0.0, 0.001, 0.5, 0.999, 1.0]:
            y = model.apply(x)
            assert CLAMP_LO <= y <= CLAMP_HI


def test_isotonic_is_monotonic():
    random.seed(7)
    preds = [i / 30 for i in range(31)]
    # outcomes correlated with preds but noisy
    outcomes = [1 if random.random() < p else 0 for p in preds]
    model = fit_isotonic(preds, outcomes)
    xs = [i / 100 for i in range(101)]
    ys = [model.apply(x) for x in xs]
    for a, b in zip(ys, ys[1:]):
        assert b >= a - 1e-9  # non-decreasing


def test_fit_calibration_selects_platt_for_few_points():
    model = fit_calibration([0.2, 0.8], [0, 1])
    assert model.method == "platt"


def test_fit_calibration_selects_isotonic_for_many_points():
    preds = [i / 20 for i in range(20)]
    outcomes = [0] * 10 + [1] * 10
    model = fit_calibration(preds, outcomes)
    assert model.method == "isotonic"


def test_empty_returns_neutral():
    model = fit_calibration([], [])
    assert model.method == "neutral"
    assert CLAMP_LO <= model.apply(0.5) <= CLAMP_HI
