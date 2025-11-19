from __future__ import annotations

import pickle
from dataclasses import dataclass
from typing import Any, Literal, Optional, Sequence, Tuple, cast

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]

from sklearn.linear_model import LogisticRegression
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import log_loss

CalibMethod = Literal["platt", "isotonic"]

# 互換API: 既存コード（core.ai.service 等）から呼ばれることを想定
def load_calibrator(path: str) -> Any:
    """
    互換ローダー。昔のコードが期待しているシグネチャに合わせる。
    core.ai.service から import される想定。
    """
    with open(path, "rb") as f:
        return pickle.load(f)

def apply_calibration(p: Sequence[float] | FloatArray, calib: Calibrator | None) -> FloatArray:
    """
    �݊��K�p�w���p�B�m���z�� p �ɑ΂��āAcalibrator ������� transform ��K�p�B
    calibrator �� None �Ȃ� p �����̂܂ܕԂ��B
    """
    data = cast(FloatArray, np.asarray(p, dtype=float))
    if calib is None:
        return data
    # Calibrator.transform �� 1�����m���x�N�g�����󂯎���� 1������Ԃ��݌v
    return calib.transform(data)


@dataclass
class Calibrator:
    method: CalibMethod
    model: LogisticRegression | IsotonicRegression

    def transform(self, p: FloatArray) -> FloatArray:
        # LogisticRegression は入力 x=logit(p)
        if self.method == "platt":
            eps = 1e-12
            x = np.clip(p, eps, 1 - eps)
            logit = np.log(x / (1 - x)).reshape(-1, 1)
            probs = self.model.predict_proba(logit)[:, 1]
            return cast(FloatArray, np.asarray(probs, dtype=float))
        elif self.method == "isotonic":
            transformed = self.model.transform(p)
            return cast(FloatArray, np.asarray(transformed, dtype=float))
        else:
            raise ValueError(f"unknown method: {self.method}")


def fit_platt(y_valid: np.ndarray, p_valid: np.ndarray) -> Calibrator:
    eps = 1e-12
    x = np.clip(p_valid, eps, 1 - eps)
    logit = np.log(x / (1 - x)).reshape(-1, 1)
    lr = LogisticRegression(solver="liblinear")
    lr.fit(logit, y_valid.astype(int))
    return Calibrator(method="platt", model=lr)


def fit_isotonic(y_valid: np.ndarray, p_valid: np.ndarray) -> Calibrator:
    ir = IsotonicRegression(out_of_bounds="clip")
    ir.fit(p_valid, y_valid.astype(float))
    return Calibrator(method="isotonic", model=ir)


def choose_best_calibrator(
    y_valid: np.ndarray, p_valid: np.ndarray
) -> Tuple[Optional[Calibrator], dict[str, Any]]:
    """
    Compare logloss across available calibration strategies and return the best.
    """
    scores: dict[str, float] = {}
    y_arr = np.asarray(y_valid, dtype=float)
    p_arr = cast(FloatArray, np.asarray(p_valid, dtype=float))
    base_ll = log_loss(y_arr, p_arr, labels=[0, 1])
    scores["none"] = base_ll

    # platt
    try:
        platt = fit_platt(y_arr, p_arr)
        p_platt = platt.transform(p_arr)
        scores["platt"] = log_loss(y_arr, p_platt, labels=[0, 1])
    except Exception:
        scores["platt"] = np.inf
        platt = None

    # isotonic
    try:
        iso = fit_isotonic(y_arr, p_arr)
        p_iso = iso.transform(p_arr)
        scores["isotonic"] = log_loss(y_arr, p_iso, labels=[0, 1])
    except Exception:
        scores["isotonic"] = np.inf
        iso = None

    best = min(scores, key=lambda k: scores[k])
    meta: dict[str, Any] = {"valid_logloss": scores, "baseline": base_ll, "selected": best}

    if best == "none":
        return None, meta
    if best == "platt" and platt is not None and scores["platt"] < base_ll:
        return platt, meta
    if best == "isotonic" and iso is not None and scores["isotonic"] < base_ll:
        return iso, meta
    return None, meta


def save_calibrator(path: str, calib: Calibrator) -> None:
    with open(path, "wb") as f:
        pickle.dump(calib, f)
