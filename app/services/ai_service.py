from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, Optional
import time

import pandas as pd
from loguru import logger

from app.services.feature_importance import compute_feature_importance
from app.services.shap_service import (
    ShapFeatureImpact,
    compute_shap_feature_importance,
    shap_items_to_frame,
)


class AISvc:
    """
    既存の推論サービス想定。モデル群は self.models に格納されている想定。
    例: self.models = {"lgbm_cls": lgb_model, "xgb_cls": xgb_model}
    """

    def __init__(self) -> None:
        self.models: Dict[str, Any] = {}
        self._fi_cache: Optional[pd.DataFrame] = None
        self._fi_cache_key: Optional[str] = None
        self._fi_cache_ts: float = 0.0

        # SHAP用の高速キャッシュ
        self._shap_cache: Optional[pd.DataFrame] = None
        self._shap_cache_key: Optional[str] = None
        self._shap_cache_ts: float = 0.0

        self.expected_features: Optional[list[str]] = None
        # ... （既存の初期化）

    # ... （既存のメソッド： load_models(), predict(), など）

    def get_feature_importance(
        self,
        method: str = "gain",
        top_n: int = 20,
        cache_sec: int = 300,
    ) -> pd.DataFrame:
        """
        GUI から呼び出して Feature Importance を取得する API。

        現状 method 引数はプレースホルダで、
        LightGBM / XGBoost の「デフォルトの重要度（おおむね gain ベース）」を返す。

        戻り値:
            columns = ["model", "feature", "importance"]
            importance は「割合(%)」を想定。FeatureImportanceWidget 側の
            軸ラベル "importance(%)" と対応させる。
        """
        model_key = ",".join(f"{name}:{id(model)}" for name, model in sorted(self.models.items()))
        key = f"{model_key}|{method}|{top_n}"
        now = time.time()

        if (
            self._fi_cache is not None
            and self._fi_cache_key == key
            and (now - self._fi_cache_ts) < cache_sec
        ):
            return self._fi_cache.copy()

        rows: list[dict[str, Any]] = []

        for name, model in self.models.items():
            if model is None:
                continue

            try:
                items = compute_feature_importance(
                    model=model,
                    feature_names=None,
                    top_n=top_n,
                )
            except Exception as e:
                print(f"[AISvc] compute_feature_importance failed for {name}: {e}")
                continue

            for item in items:
                rows.append(
                    {
                        "model": name,
                        "feature": item.name,
                        "importance": item.importance_pct,
                    }
                )

        if not rows:
            df = pd.DataFrame(columns=["model", "feature", "importance"])
        else:
            df = pd.DataFrame(rows)

        self._fi_cache = df.copy()
        self._fi_cache_key = key
        self._fi_cache_ts = now
        return df

    def _load_shap_background_features(
        self,
        max_rows: int = 2000,
        *,
        csv_path: Path | None = None,
    ) -> pd.DataFrame:
        """
        SHAP計算用の背景特徴量を読み込むヘルパ。

        暫定仕様：
        - data/USDJPY/features_for_shap.csv に特徴量CSVがある前提。
          （今後、weekly_retrain 側から自動出力させる予定）
        - self.expected_features があれば、その列順に揃える。
        """
        if csv_path is None:
            csv_path = Path("data") / "USDJPY" / "features_for_shap.csv"

        if not csv_path.exists():
            raise FileNotFoundError(
                f"SHAP用特徴量CSVが見つかりません: {csv_path}\n"
                "一時的には手動で特徴量CSVを用意してください。"
            )

        logger.info(
            "SHAP背景特徴量を読み込み: path={path}", path=csv_path.as_posix()
        )
        df = pd.read_csv(csv_path)

        if self.expected_features:
            missing = set(self.expected_features) - set(df.columns)
            if missing:
                raise ValueError(
                    "SHAP背景特徴量に expected_features の列が足りません: "
                    f"{sorted(missing)}"
                )
            df = df.loc[:, list(self.expected_features)]

        if len(df) > max_rows:
            df = df.sample(n=max_rows, random_state=42)

        return df

    def get_shap_top_features(
        self,
        *,
        top_n: int = 20,
        max_background: int = 2000,
        csv_path: Path | None = None,
        cache_sec: int = 300,
    ) -> pd.DataFrame:
        """
        LightGBMモデルに対する SHAP グローバル重要度（平均絶対SHAP）を計算し、
        DataFrame (rank, feature, mean_abs_shap, model) を返す。

        - 現状は LightGBM 系モデル（キー名に 'lgb' を含むもの）を対象。
        - 背景データは _load_shap_background_features() で読み込む。
        - cache_sec 秒以内に同じ条件で呼ばれた場合は前回結果を再利用する。
        """
        model_key = ",".join(
            f"{name}:{id(model)}"
            for name, model in sorted(self.models.items())
        )

        if csv_path is None:
            csv_real = Path("data") / "USDJPY" / "features_for_shap.csv"
        else:
            csv_real = csv_path

        try:
            stat = csv_real.stat()
            csv_sig = f"{csv_real.resolve()}|{int(stat.st_mtime)}|{stat.st_size}"
        except FileNotFoundError:
            csv_sig = f"{csv_real.resolve()}|missing"

        key = f"{model_key}|{csv_sig}|top={top_n}|bg={max_background}"
        now = time.time()

        if (
            self._shap_cache is not None
            and self._shap_cache_key == key
            and (now - self._shap_cache_ts) < cache_sec
        ):
            return self._shap_cache.copy()

        target_name: Optional[str] = None
        target_model: Any | None = None

        for name, model in self.models.items():
            if "lgb" in name.lower():
                target_name = name
                target_model = model
                break

        if target_model is None:
            raise RuntimeError(
                "SHAP計算対象の LightGBM モデルが見つかりませんでした。"
                "AISvc.models に 'lgb' を含むキーで LightGBM を登録してください。"
            )

        logger.info(
            "SHAP計算対象モデル: name={name}, type={typ}",
            name=target_name,
            typ=type(target_model).__name__,
        )

        df_bg = self._load_shap_background_features(
            max_rows=max_background,
            csv_path=csv_real,
        )

        feature_names = (
            list(self.expected_features)
            if self.expected_features
            else list(df_bg.columns)
        )

        items: list[ShapFeatureImpact] = compute_shap_feature_importance(
            target_model,
            df_bg,
            feature_names=feature_names,
            top_n=top_n,
            max_background=max_background,
        )

        df_result = shap_items_to_frame(items)
        df_result.insert(0, "model", target_name)

        self._shap_cache = df_result.copy()
        self._shap_cache_key = key
        self._shap_cache_ts = now

        return df_result
