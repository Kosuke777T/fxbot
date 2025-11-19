# core/utils/hashing.py
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_features(features: Mapping[str, Any], order: Sequence[str] | None = None) -> str:
    """
    特徴量辞書を安定ハッシュ化する。
    order が与えられたらその順、無ければキーでソート。
    値は JSON にしてから sha256。
    """
    if order is None:
        order = sorted(features.keys())
    snapshot = {key: features.get(key, None) for key in order}
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(payload)
