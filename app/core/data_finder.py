# app/core/data_finder.py
from __future__ import annotations
import os, glob
from pathlib import Path
from typing import Iterable, Optional, Tuple
import pandas as pd

def _expand_path(p: str) -> Iterable[Path]:
    # 環境変数・%APPDATA% を解決し、glob を展開
    p = os.path.expandvars(p)
    p = os.path.expanduser(p)
    for hit in glob.glob(p, recursive=True):
        yield Path(hit)

def _load_csv(path: Path) -> Optional[pd.DataFrame]:
    try:
        df = pd.read_csv(path)
        low = {c.lower(): c for c in df.columns}
        need = ["time","open","high","low","close"]
        if not all(k in low for k in need):
            return None
        return df
    except Exception:
        return None

def resolve_csv(symbol: str, timeframe: str, search_paths: Iterable[str]) -> Tuple[Optional[Path], Optional[pd.DataFrame]]:
    """search_paths から {SYMBOL}_{TF}.csv を探して返す。最初に見つかったものを採用。"""
    target_name = f"{symbol.upper()}_{timeframe.upper()}.csv"
    for base in search_paths:
        for base_path in _expand_path(base):
            if not base_path.exists():
                continue
            # 1) 直下にある場合
            p1 = base_path / target_name
            if p1.exists():
                df = _load_csv(p1)
                if df is not None:
                    return p1, df
            # 2) サブフォルダも探索
            for hit in base_path.rglob(target_name):
                df = _load_csv(hit)
                if df is not None:
                    return hit, df
    return None, None
