"""Calibration without sklearn.

Two methods:
* Platt scaling (logistic) — used as a fallback when there are few resolutions.
* Isotonic regression via the Pool Adjacent Violators (PAV) algorithm — used
  when there are enough data points. Produces a monotonic, piecewise-constant
  mapping.

All calibrated outputs are clamped to ``[0.01, 0.99]``. A neutral map (identity
with a light push away from 0.5) is used before any data exists, reflecting the
tendency of LLMs to hedge.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

CLAMP_LO = 0.01
CLAMP_HI = 0.99
MIN_ISOTONIC_POINTS = 10


def _clamp(p: float) -> float:
    return max(CLAMP_LO, min(CLAMP_HI, p))


def neutral_map(push: float = 0.08) -> "CalibrationModel":
    """Identity-ish map that nudges predictions away from 0.5."""
    return CalibrationModel(method="neutral", params={"push": push}, points=[])


@dataclass
class CalibrationModel:
    method: str  # neutral | platt | isotonic
    params: dict
    points: list[list[float]]  # sorted [[x, y], ...] for isotonic
    n: int = 0

    def apply(self, p_raw: float) -> float:
        p_raw = max(0.0, min(1.0, p_raw))
        if self.method == "neutral":
            push = self.params.get("push", 0.08)
            # push away from 0.5
            adjusted = p_raw + push * (p_raw - 0.5) * 2
            return _clamp(adjusted)
        if self.method == "platt":
            a = self.params["a"]
            b = self.params["b"]
            z = a * p_raw + b
            return _clamp(1.0 / (1.0 + math.exp(-z)))
        if self.method == "isotonic":
            return _clamp(_interp_isotonic(self.points, p_raw))
        return _clamp(p_raw)


def _interp_isotonic(points: list[list[float]], x: float) -> float:
    if not points:
        return x
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for i in range(1, len(xs)):
        if x <= xs[i]:
            x0, x1 = xs[i - 1], xs[i]
            y0, y1 = ys[i - 1], ys[i]
            if x1 == x0:
                return y1
            t = (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return ys[-1]


def fit_platt(
    preds: list[float], outcomes: list[int], *, iters: int = 200, lr: float = 0.1
) -> CalibrationModel:
    """Fit a 1-D logistic (Platt) model via gradient descent."""
    a, b = 1.0, 0.0
    n = len(preds)
    if n == 0:
        return neutral_map()
    for _ in range(iters):
        ga = gb = 0.0
        for x, y in zip(preds, outcomes):
            z = a * x + b
            pred = 1.0 / (1.0 + math.exp(-max(-30, min(30, z))))
            err = pred - y
            ga += err * x
            gb += err
        a -= lr * ga / n
        b -= lr * gb / n
    return CalibrationModel(method="platt", params={"a": a, "b": b}, points=[], n=n)


def fit_isotonic(preds: list[float], outcomes: list[int]) -> CalibrationModel:
    """Isotonic regression via Pool Adjacent Violators (PAV)."""
    pairs = sorted(zip(preds, outcomes), key=lambda t: t[0])
    xs = [p for p, _ in pairs]
    ys = [float(o) for _, o in pairs]
    n = len(ys)
    if n == 0:
        return neutral_map()

    # PAV blocks are (fitted value, weight, sample count).
    stack: list[tuple[float, float, int]] = []
    for value in ys:
        stack.append((value, 1.0, 1))
        while len(stack) > 1 and stack[-2][0] > stack[-1][0]:
            v2, w2, c2 = stack.pop()
            v1, w1, c1 = stack.pop()
            merged_weight = w1 + w2
            merged_value = (v1 * w1 + v2 * w2) / merged_weight
            stack.append((merged_value, merged_weight, c1 + c2))

    fitted: list[float] = []
    for value, _weight, count in stack:
        fitted.extend([value] * count)

    # Build monotonic knots (x, y) — collapse consecutive equal x.
    points: list[list[float]] = []
    for x, y in zip(xs, fitted):
        if points and abs(points[-1][0] - x) < 1e-12:
            points[-1][1] = y
        else:
            points.append([x, y])
    return CalibrationModel(method="isotonic", params={}, points=points, n=n)


def fit_calibration(preds: list[float], outcomes: list[int]) -> CalibrationModel:
    """Choose a method based on the number of available resolutions."""
    n = len(preds)
    if n == 0:
        return neutral_map()
    if n < MIN_ISOTONIC_POINTS:
        return fit_platt(preds, outcomes)
    return fit_isotonic(preds, outcomes)


def model_from_doc(doc: dict) -> CalibrationModel:
    return CalibrationModel(
        method=doc.get("method", "neutral"),
        params=doc.get("params", {}),
        points=doc.get("points", []),
        n=doc.get("n_resolutions", 0),
    )


def brier_score(p: float, outcome: int) -> float:
    return (p - outcome) ** 2


def log_score(p: float, outcome: int) -> float:
    p = _clamp(p)
    return -(outcome * math.log(p) + (1 - outcome) * math.log(1 - p))
