from __future__ import annotations

import os
from pathlib import Path


def get_project_root() -> Path:
    """
    fxbot プロジェクトのルートパスを返す。
    このファイル自身（fxbot_path.py）が置かれているディレクトリをルートとみなす。
    例:
      C:\\Users\\macht\\OneDrive\\fxbot
      D:\\macht\\OneDrive\\fxbot
      C:\\fxbot
    """
    return Path(__file__).resolve().parent


def get_data_root(cli_data_dir: str | os.PathLike | None = None) -> Path:
    """
    データディレクトリの候補を複数試して、最初に存在したディレクトリを採用する。
    優先順位:
      1) --data-dir 引数で明示されたパス
      2) 環境変数 FXBOT_DATA
      3) プロジェクトルート配下の data/
      4) カレントディレクトリ配下の data/
    どれも存在しない場合は、最後に project_root/data を返す（存在チェックには使える）。
    """
    candidates: list[Path] = []

    # 1) CLI 引数
    if cli_data_dir:
        candidates.append(Path(cli_data_dir))

    # 2) 環境変数
    env_dir = os.getenv("FXBOT_DATA")
    if env_dir:
        candidates.append(Path(env_dir))

    # 3) プロジェクトルートの data
    root = get_project_root()
    candidates.append(root / "data")

    # 4) カレントディレクトリの data
    candidates.append(Path.cwd() / "data")

    # 実在するもののうち先頭
    for p in candidates:
        try:
            if p.is_dir():
                return p.resolve()
        except Exception:
            # パス参照で問題が起きた場合は次へ
            continue

    # 全滅なら project_root/data をとりあえず返す（存在しない場合でも作成されうる）
    return (root / "data").resolve()


def _ensure_dir(p: Path) -> Path:
    """
    ディレクトリが存在しないなら作成して Path を返す。
    Windows/OneDrive 等でパスが特殊でも例外を上げずに済むようにする。
    """
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        # 作成できない場合でも Path を返す（呼び出し側でハンドリング）
        pass
    return p


def get_file_tag(symbol: str) -> str:
    """
    CSV ファイル名に使う接尾辞なしタグを決定する（英字のみ）。
    例: "USDJPY-" -> "USDJPY" , "USDJPY.m" -> "USDJPY"
    """
    tag = "".join([c for c in (symbol or "") if c.isalpha()])
    return tag or symbol


def get_ohlcv_csv_path(
    symbol: str,
    timeframe: str,
    data_root: str | os.PathLike | Path | None = None,
    layout: str = "per-symbol",
) -> Path:
    """
    統一された場所へ OHLCV CSV のパスを返す。必要なディレクトリは作成する。

    - `symbol`: ブローカー接尾辞を含む可能性あり（ここではそのまま受け取る）
    - `timeframe`: 例 'M5', 'H1'（そのままファイル名に使う）
    - `data_root`: None の場合は `get_data_root()` に委譲
    - `layout`: 'per-symbol' または 'flat'

    返り値例（per-symbol）: <data_root>/USDJPY/ohlcv/USDJPY_M5.csv
    返り値例（flat）: <data_root>/USDJPY_M5.csv
    """
    # data_root を決定
    if data_root is None:
        root = get_data_root()
    else:
        root = Path(data_root) if not isinstance(data_root, Path) else data_root
        if not root.is_absolute():
            root = (get_project_root() / root).resolve()

    root = root.resolve()

    # file tag
    tag = get_file_tag(symbol.upper())

    if layout == "per-symbol":
        ohlcv_dir = root / tag / "ohlcv"
        _ensure_dir(ohlcv_dir)
        csv_path = ohlcv_dir / f"{tag}_{timeframe}.csv"
    else:
        _ensure_dir(root)
        csv_path = root / f"{tag}_{timeframe}.csv"

    return csv_path.resolve()
