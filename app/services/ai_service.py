from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, Optional
import time
import json

import numpy as np
import pandas as pd
from loguru import logger
import joblib

from core.ai.loader import _load_active_meta

from app.services.feature_importance import compute_feature_importance
from app.services.shap_service import (
    ShapFeatureImpact,
    compute_shap_feature_importance,
    shap_items_to_frame,
)
from app.services.edition_guard import get_capability

# AISvc.predict の詳細ログを出すかどうか（バックテストでは OFF 推奨）
DEBUG_PREDICT_LOG = False


def _safe_float(value: Any) -> Optional[float]:
    """数値 or 数値っぽい文字列だけ float に変換し、それ以外は None を返す小さいユーティリティ。"""
    try:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str) and value.strip():
            return float(value)
    except Exception:
        return None
    return None


def get_model_metrics(models_dir: str | Path = "models") -> Dict[str, Any]:
    """
    active_model.json からモデル指標情報を取得する。

    戻り値のキー:
        - model_name   : str          # 例: "LightGBM_clf"
        - version      : str | None   # 例: "20251127_075810"
        - file         : str | None   # 例: "LightGBM_clf_20251127_075810.pkl"
        - best_threshold : float | None
        - logloss      : float | None
        - auc          : float | None
        - trained_at   : str | None   # active_model.json の trained_at
    """
    import os
    base = Path(models_dir)

    # --- active_model.json を読む ---------------------------------
    active_path = base / "active_model.json"
    active: Dict[str, Any] = {}
    if active_path.is_file():
        try:
            active = json.loads(active_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"[AISvc] failed to read {active_path}: {e}")
    else:
        logger.info("[AISvc] active_model.json not found; model metrics will be None.")

    # --- model_path からファイル名とモデル名を抽出 ----------------
    model_file = active.get("file") or active.get("model_file")
    file_name: Optional[str] = None
    model_name: Optional[str] = None

    if isinstance(model_file, str) and model_file:
        # file = os.path.basename(model_path)
        file_name = os.path.basename(model_file)
        # model_name = file.split("_clf")[0] + "_clf" くらいの簡易でOK
        if "_clf" in file_name:
            model_name = file_name.split("_clf")[0] + "_clf"
        else:
            # _clf がない場合は拡張子を除いたファイル名をそのまま使用
            model_name = Path(file_name).stem

    # active_model.json から直接取得を試みる
    if not model_name:
        model_name = active.get("model_name")

    # --- active_model.json から直接取得 --------------------------
    best_threshold = active.get("best_threshold")
    trained_at = active.get("trained_at") or active.get("trained_at_jst") or active.get("updated_at")
    version = active.get("version")
    if isinstance(version, (int, float)):
        version = str(version)

    # --- metrics が存在していれば、そこから logloss / auc を取り出す ---
    metrics = active.get("metrics") or {}
    logloss = _safe_float(metrics.get("logloss"))
    auc = _safe_float(metrics.get("auc@cal") or metrics.get("auc"))

    # meta.json からも metrics を取得（active_model.json にない場合のフォールバック）
    if (logloss is None or auc is None) and model_file:
        meta_file_name = active.get("meta_file")
        meta_path: Optional[Path] = None

        # 1) active_model.json の meta_file 優先
        if isinstance(meta_file_name, str) and meta_file_name:
            cand = base / meta_file_name
            if cand.is_file():
                meta_path = cand

        # 2) なければ model_file から {stem}.meta.json を推測
        if meta_path is None and isinstance(model_file, str) and model_file:
            cand = base / f"{Path(model_file).stem}.meta.json"
            if cand.is_file():
                meta_path = cand

        # 3) meta_path が決まっていれば読む
        if meta_path is not None:
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                meta_metrics = meta.get("metrics") or {}
                if logloss is None:
                    logloss = _safe_float(meta_metrics.get("logloss"))
                if auc is None:
                    auc = _safe_float(meta_metrics.get("auc@cal") or meta_metrics.get("auc"))
            except Exception as e:
                logger.warning(f"[AISvc] failed to read {meta_path}: {e}")

    # --- まとめて返す ---------------------------------------------
    result: Dict[str, Any] = {
        "model_name": model_name,
        "version": version,
        "file": file_name,
        "best_threshold": _safe_float(best_threshold),
        "logloss": logloss,
        "auc": auc,
        "trained_at": trained_at,
    }

    return result


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
        self.models_dir = Path("models")
        self._active_meta: Optional[dict[str, Any]] = None

        # SHAP用の高速キャッシュ
        self._shap_cache: Optional[pd.DataFrame] = None
        self._shap_cache_key: Optional[str] = None
        self._shap_cache_ts: float = 0.0

        self.expected_features: Optional[list[str]] = None
        self.calibrator_name: str = "none"  # execution_stub.py で参照される属性
        self.model_name: str = "unknown"  # execution_stub.py で参照される属性

        # ★ここを追加：起動時に一度だけ active_model.json と同期
        self._sync_expected_features()
        # ... （既存の初期化）

    def _normalize_features_for_model(self, feats: "Mapping[str, float]") -> "dict[str, float]":
        """
        モデルの expected_features に合わせて特徴量を揃える。
        - expected_features が設定されていれば、その順番・その列だけに揃える
        - 足りない列は 0.0 で補完する
        - expected_features が None/空なら、そのまま dict(feats) を返す
        """
        from collections.abc import Mapping

        if not isinstance(feats, Mapping):
            # 万一 Series や list などが来た時のガード
            feats = dict(feats)

        if not self.expected_features:
            return dict(feats)

        normalized: dict[str, float] = {}
        for name in self.expected_features:
            value = feats.get(name, 0.0)
            # float に変換しておく（np.array にそのまま突っ込めるように）
            try:
                normalized[name] = float(value)
            except (TypeError, ValueError):
                normalized[name] = 0.0
        return normalized


    def _sync_expected_features(self) -> None:
        """
        active_model.json / モデル本体から expected_features を
        self.expected_features に一度だけコピーする。
        """
        # すでに設定済みなら何もしない
        if self.expected_features:
            return

        # 1) モデルオブジェクト側に expected_features があれば優先して使う
        for model in self.models.values():
            exp = getattr(model, "expected_features", None)
            if exp:
                # list, tuple, np.ndarray などを list に揃える
                self.expected_features = list(exp)
                logger.info(
                    f"[AISvc] expected_features synced from model ({len(self.expected_features)} cols)"
                )
                return

        # 2) active_model.json 自体に feature_order/features があれば最優先で採用
        active_features = None
        try:
            active_path = Path("models") / "active_model.json"
            if active_path.is_file():
                active = json.loads(active_path.read_text(encoding="utf-8"))
                if isinstance(active, dict):
                    active_features = active.get("feature_order") or active.get("features")
        except Exception as exc:
            logger.warning(f"[AISvc] failed to read active_model.json for expected_features: {exc}")

        if active_features and isinstance(active_features, (list, tuple)) and active_features:
            self.expected_features = list(active_features)
            logger.info(
                f"[AISvc] expected_features loaded from active_model.json ({len(self.expected_features)} cols)"
            )
            return

        # 3) fallback: active_model.json → meta_file → feature_order or features
        try:
            meta = _load_active_meta()
        except Exception as exc:
            logger.warning(f"[AISvc] failed to load active meta for expected_features: {exc}")
            return

        seq = meta.get("feature_order") or meta.get("features")
        if isinstance(seq, (list, tuple)) and seq:
            self.expected_features = list(seq)
            logger.info(
                f"[AISvc] expected_features loaded from meta_file ({len(self.expected_features)} cols)"
            )

    # ここから追加 ------------------------------------------------------------
    class ProbOut:
        """
        AISvc 内部で使うだけのシンプルな入出力コンテナ。
        get_live_probs() では dict に変換するので、外部から直接触ることは想定していない。
        """
        def __init__(self, p_buy: float, p_sell: float, p_skip: float = 0.0) -> None:
            self.p_buy = float(p_buy)
            self.p_sell = float(p_sell)
            self.p_skip = float(p_skip)

    def _ensure_model_loaded(self) -> None:
        """
        self.models に推論用モデルが未ロードなら、active_model.json を見てロードする。
        - models/<file> を joblib.load で読み込む想定
        - 1つ目のモデルを LightGBM と見なして使う
        """
        if self.models:
            # すでに何かしらモデルが入っていれば何もしない
            return

        try:
            meta = _load_active_meta()
        except Exception as exc:
            logger.error("[AISvc] active model meta の読み込みに失敗: {err}", err=exc)
            return

        self._active_meta = meta

        # model_path を取得（フォールバック: file から models/<file> を組み立て）
        model_path = meta.get("model_path")
        if not model_path:
            # model_path が None/空の場合、file から models/<file> を組み立て
            file = meta.get("file")
            if file:
                p = Path("models") / file
                if p.exists():
                    model_path = str(p)

        if not model_path:
            logger.error("[AISvc] active_model.json に 'model_path' または 'file' がありません")
            return

        # model_path を Path オブジェクトに変換
        model_path_obj = Path(model_path)
        if not model_path_obj.exists():
            logger.error("[AISvc] モデルファイルが見つかりません: {path}", path=model_path_obj.as_posix())
            return

        try:
            model = joblib.load(model_path_obj)
            logger.info("[AISvc] pickleがロードされました: path={path}", path=model_path_obj.as_posix())
        except Exception as exc:
            logger.error("[AISvc] モデルのロードに失敗: path={path} err={err}",
                         path=model_path_obj.as_posix(), err=exc)
            return

        # とりあえず 'lgbm' というキーで登録（SHAP などから参照される）
        self.models["lgbm"] = model
        logger.info("[AISvc] model loaded: key='lgbm', type={typ}",
                    typ=type(model).__name__)

        # モデル側が feature_name / expected_features を持っていて、
        # まだ expected_features がセットされていなければ同期しておく
        if not self.expected_features:
            exp = getattr(model, "expected_features", None)
            if exp:
                self.expected_features = list(exp)
                logger.info(
                    "[AISvc] expected_features synced from model ({n} cols)",
                    n=len(self.expected_features),
                )
            else:
                # LightGBM Booster なら feature_name() で列名が取れることが多い
                feat_names = None
                try:
                    feat_names = model.feature_name()
                except Exception:
                    feat_names = None

                if feat_names:
                    self.expected_features = list(feat_names)
                    logger.info(
                        "[AISvc] expected_features synced from model.feature_name() ({n} cols)",
                        n=len(self.expected_features),
                    )

    def predict(self, X: np.ndarray | Dict[str, float], *, no_metrics: bool = False) -> "AISvc.ProbOut":
        """
        単一サンプルの特徴量を受け取り、p_buy / p_sell / p_skip を返す。

        Parameters
        ----------
        X : np.ndarray | Dict[str, float]
            - np.ndarray: shape [1, n_features] の特徴量ベクトル
            - Dict[str, float]: 特徴量名をキーとした辞書（ExecutionStub 互換）
        no_metrics : bool, optional
            True の場合、metrics の更新を行わない（デフォルト: False）

        Returns
        -------
        AISvc.ProbOut
            p_buy, p_sell, p_skip を含む予測結果
        """
        self._ensure_model_loaded()

        # Dict[str, float] が渡された場合は np.ndarray に変換
        if isinstance(X, dict):
            # expected_features に基づいてベクトル化
            model_feats = self._normalize_features_for_model(X)
            if self.expected_features:
                vec = [model_feats.get(name, 0.0) for name in self.expected_features]
            else:
                vec = list(model_feats.values())
            X = np.array([vec], dtype=float)
        else:
            # np.ndarray の場合も expected_features に合わせて変換
            if isinstance(X, (list, tuple)):
                X = np.array(X, dtype=float)
            if X.ndim == 1:
                X = X.reshape(1, -1)

        # --- デバッグ: モデル入力の shape と1行目をログに出す ---
        if DEBUG_PREDICT_LOG:
            try:
                row_preview = None
                if hasattr(X, "__getitem__"):
                    # X[0] が numpy 配列や list のことを想定
                    row0 = X[0]
                    # numpy でも list でも .tolist() が使えるようにする
                    row_preview = getattr(row0, "tolist", lambda: row0)()
                logger.debug(
                    "[AISvc.predict] X.shape={shape}, X[0]={row}",
                    shape=getattr(X, "shape", None),
                    row=row_preview,
                )
            except Exception as e:
                logger.warning("[AISvc.predict] debug logging failed: {err}", err=e)

        if not self.models:
            # モデルが 1つもない場合は安全側に全スキップ
            logger.error("[AISvc.predict] モデルがロードされていません。全スキップを返します。")
            return AISvc.ProbOut(0.0, 0.0, 1.0)

        # ひとまず最初のモデルを使う（現状 1 モデル想定）
        model = next(iter(self.models.values()))

        try:
            # sklearn 互換モデルの場合
            if hasattr(model, "predict_proba"):
                proba = model.predict_proba(X)
                proba = np.asarray(proba)
                if proba.ndim == 2 and proba.shape[1] >= 2:
                    p_buy = float(proba[0, 1])
                else:
                    p_buy = float(proba[0])
            else:
                # LightGBM Booster など: predict がそのまま「陽線クラスの確率」を返す前提
                y_pred = model.predict(X)
                y_pred = np.asarray(y_pred)
                p_buy = float(y_pred[0])
        except Exception as exc:
            logger.error("[AISvc.predict] 推論に失敗: {err}", err=exc)
            return AISvc.ProbOut(0.0, 0.0, 1.0)

        # 0〜1 にクリップしておく
        p_buy = max(0.0, min(1.0, p_buy))
        p_sell = 1.0 - p_buy
        p_skip = 0.0

        return AISvc.ProbOut(p_buy, p_sell, p_skip)
    # ここまで追加 ------------------------------------------------------------


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
        # ★ モデル未ロード時でもここで遅延ロードしておく
        self._ensure_model_loaded()
        if not self.models:
            # モデルが1つもロードできなかった場合は、空の DataFrame を返す
            return pd.DataFrame(columns=["model", "feature", "importance"])

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

        # --- EditionGuard による制限 ---
        from app.services.edition_guard import EditionGuard
        eg = EditionGuard()
        level = eg.get_capability("fi_level") or 0

        # level 0: 表示なし
        if level == 0:
            return df.iloc[0:0]  # カラム構造だけ残した空DF

        # level 1: Top3
        if level == 1:
            return df.head(3) if len(df) > 0 else df

        # level 2: Top20
        if level == 2:
            return df.head(20) if len(df) > 0 else df

        # level 3: 全件
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

        # ✅ SHAP入力も予測と同じ正規化を通す（missingで落ちない）
        if self.expected_features:
            # expected_features に基づいて列を補完（不足分は 0.0）
            for feat_name in self.expected_features:
                if feat_name not in df.columns:
                    df[feat_name] = 0.0
            # expected_features の順序に合わせて列を並び替え
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
        # ★ ここでも必要ならモデルを遅延ロード
        self._ensure_model_loaded()

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

        # --- EditionGuard による制限 ---
        from app.services.edition_guard import EditionGuard
        eg = EditionGuard()
        level = eg.get_capability("shap_level") or 0

        # level 0: 表示なし
        if level == 0:
            return df_result.iloc[0:0]  # カラム構造だけ残した空DF

        # level 1: Top3
        if level == 1:
            return df_result.head(3) if len(df_result) > 0 else df_result

        # level 2: Top20
        if level == 2:
            return df_result.head(20) if len(df_result) > 0 else df_result

        # level 3: 全件
        return df_result

    def get_shap_values(self):
        """
        SHAP 結果を EditionGuard に従って制限して返す。
        戻り値は DataFrame でも list でもOKなように扱う。

        注意: このメソッドはモデル側に get_shap_values() メソッドがあることを前提とします。
        モデル側に実装がない場合は、get_shap_top_features() を使用してください。
        """
        from app.services.edition_guard import EditionGuard
        eg = EditionGuard()
        level = eg.get_capability("shap_level") or 0

        # モデルが SHAP未対応のときにも落ちないように try/except
        try:
            # まずモデルをロード
            self._ensure_model_loaded()
            if not self.models:
                return [] if level == 0 else []

            # 最初のモデルを取得
            model = next(iter(self.models.values()))

            # モデルに get_shap_values() メソッドがあるか確認
            if not hasattr(model, "get_shap_values"):
                # モデル側に実装がない場合は空を返す
                return [] if level == 0 else []

            shap_vals = model.get_shap_values()
        except AttributeError:
            # まだ SHAP 実装していないモデルの場合
            return [] if level == 0 else []
        except Exception as e:
            logger.warning(f"[AISvc.get_shap_values] SHAP取得エラー: {e}")
            return [] if level == 0 else []

        if shap_vals is None:
            return [] if level == 0 else []

        # level 0: 表示なし
        if level == 0:
            # DataFrame の場合は空DF、listの場合は []
            if hasattr(shap_vals, "iloc"):
                return shap_vals.iloc[0:0]
            return []

        # level 1: Top3
        if level == 1:
            return shap_vals.head(3) if hasattr(shap_vals, "head") else shap_vals[:3]

        # level 2: Top20
        if level == 2:
            return shap_vals.head(20) if hasattr(shap_vals, "head") else shap_vals[:20]

        # level 3: 全件
        return shap_vals

    def feature_importance(self) -> pd.DataFrame:
        """FI を edition に応じて TopN で返す。"""
        fi_level = get_capability("fi_level")
        if fi_level == 0:
            return pd.DataFrame(columns=["model", "feature", "importance"])

        # モデルロード（失敗時は空データ）
        self._ensure_model_loaded()
        if not self.models:
            return pd.DataFrame(columns=["model", "feature", "importance"])

        # 最初のモデルを取得
        model = next(iter(self.models.values()))

        # LightGBM 想定（他モデルの場合は後で拡張）
        try:
            importances = model.feature_importances_
            features = self.expected_features
            if features is None:
                # expected_features がない場合は feature_name() を試す
                try:
                    features = model.feature_name()
                except Exception:
                    return pd.DataFrame(columns=["model", "feature", "importance"])
        except Exception:
            return pd.DataFrame(columns=["model", "feature", "importance"])

        if len(importances) != len(features):
            logger.warning(
                "[AISvc.feature_importance] importances length ({}) != features length ({})",
                len(importances),
                len(features),
            )
            return pd.DataFrame(columns=["model", "feature", "importance"])

        df = pd.DataFrame({
            "model": ["active"] * len(features),
            "feature": features,
            "importance": importances
        }).sort_values("importance", ascending=False)

        # TopN に切り替え
        if fi_level == 1:
            top_n = 3
        elif fi_level == 2:
            top_n = 20
        else:
            top_n = None

        if top_n is not None:
            df = df.head(top_n)

        return df.reset_index(drop=True)

    def shap_summary(self) -> Dict[str, Any]:
        """SHAP を edition に応じて TopN で返す。"""
        shap_level = get_capability("shap_level")
        if shap_level == 0:
            return {"features": [], "values": []}

        self._ensure_model_loaded()
        if not self.models:
            return {"features": [], "values": []}

        # 最初のモデルを取得
        model = next(iter(self.models.values()))

        try:
            import shap
            # Lazy SHAP
            explainer = shap.TreeExplainer(model)

            # 背景データを取得（既存のメソッドを使用）
            try:
                bg_features = self._load_shap_background_features(max_rows=100)
            except Exception:
                # 背景データがない場合は空を返す
                return {"features": [], "values": []}

            shap_values = explainer.shap_values(bg_features)
        except Exception as e:
            logger.warning(f"[AISvc.shap_summary] SHAP計算エラー: {e}")
            return {"features": [], "values": []}

        # shap_values が [classes][samples][features] 形式の場合
        if isinstance(shap_values, list):
            shap_values = shap_values[0]

        mean_abs = np.mean(np.abs(shap_values), axis=0)
        features = self.expected_features

        if features is None:
            try:
                features = model.feature_name()
            except Exception:
                return {"features": [], "values": []}

        if len(mean_abs) != len(features):
            logger.warning(
                "[AISvc.shap_summary] mean_abs length ({}) != features length ({})",
                len(mean_abs),
                len(features),
            )
            return {"features": [], "values": []}

        df = pd.DataFrame({
            "feature": features,
            "value": mean_abs
        }).sort_values("value", ascending=False)

        # TopN 制御
        if shap_level == 1:
            top_n = 3
        elif shap_level == 2:
            top_n = 20
        else:
            top_n = None

        if top_n is not None:
            df = df.head(top_n)

        return {
            "features": df["feature"].tolist(),
            "values": df["value"].tolist()
        }

    def get_live_probs(self, symbol: str) -> dict[str, float]:
        """
        Live 用：execution_stub と同じ特徴量パイプラインを使って
        確率と atr_for_lot を返す簡易版。
        """
        from app.core import market
        from app.core.config_loader import load_config
        from app.services.execution_stub import _collect_features

        # ★ まず expected_features を active_model.json と同期しておく
        self._sync_expected_features()

        # tick が取れない場合は素直に全部 SKIP に倒す
        try:
            tick = market.tick(symbol)
        except Exception as e:
            logger.warning(
                "[AISvc.get_live_probs] tick取得に失敗: symbol={symbol} error={err}",
                symbol=symbol,
                err=e,
            )
            tick = None

        if not tick:
            logger.warning(
                "[AISvc.get_live_probs] tickが取得できないため AIスキップ: symbol={symbol}",
                symbol=symbol,
            )
            return {
                "p_buy": 0.0,
                "p_sell": 0.0,
                "p_skip": 1.0,
                "atr_for_lot": 0.0,
            }

        # 設定から base_features を取得（execution_stub と揃える）
        try:
            cfg = load_config()
        except Exception:
            cfg = {}

        ai_cfg = cfg.get("ai", {}) if isinstance(cfg, dict) else {}
        base_features = tuple(ai_cfg.get("features", {}).get("base", []))

        # spread を market から取得（なければ 0.0）
        try:
            spr_callable = getattr(market, "spread_pips", None)
            spread_pips = spr_callable(symbol) if callable(spr_callable) else 0.0
        except Exception:
            spread_pips = 0.0

        # 現在のオープンポジション数は、とりあえず 0 として扱う
        open_positions = 0

        # execution_stub と同じロジックで特徴量を収集
        features = _collect_features(
            symbol,
            base_features,
            tick,
            spread_pips,
            open_positions,
        )

        # モデル入力向けに列を揃える
        model_feats = self._normalize_features_for_model(features)

        # expected_features があればその順でベクトル化
        if self.expected_features:
            vec = [model_feats[name] for name in self.expected_features]
        else:
            vec = list(model_feats.values())

        # --- デバッグ: 特徴量 dict & 並び替え後ベクトルをログ出力 ---
        if DEBUG_PREDICT_LOG:
            try:
                import json
                logger.debug(
                    "[AISvc.get_live_probs] model_feats(normalized)={payload}",
                    payload=json.dumps(model_feats, ensure_ascii=False),
                )
                logger.debug(
                    "[AISvc.get_live_probs] input vec (ordered)={vec}",
                    vec=vec,
                )
            except Exception as e:
                logger.warning(
                    "[AISvc.get_live_probs] failed to dump debug input: {err}",
                    err=e,
                )

        arr = np.array([vec], dtype=float)

        # 予測
        prob = self.predict(arr)

        # ロット計算用 ATR（price 単位）に必要な atr_14 と揃えておく
        atr_for_lot = float(model_feats.get("atr_14", 0.0))


        return {
            "p_buy": float(prob.p_buy),
            "p_sell": float(prob.p_sell),
            "p_skip": float(prob.p_skip),
            "atr_for_lot": atr_for_lot,
        }


    def build_decision_from_probs(self, probs: dict, symbol: str) -> dict:
        """
        Live 用：execution_stub の ENTRY/SKIP 判定を最小限で再現。
        ATR や threshold は設定ファイルを参照する。
        """
        from app.core.config_loader import load_config
        cfg = load_config()
        thr = float(cfg.get("entry", {}).get("prob_threshold", 0.5))

        p_buy = probs["p_buy"]
        p_sell = probs["p_sell"]

        # margin を導入（拮抗時をSKIPするための閾値）
        margin = 0.03

        # confidence と diff を計算
        confidence = max(p_buy, p_sell)
        diff = abs(p_buy - p_sell)

        # SKIP 条件1: confidence が threshold 未満
        if confidence < thr:
            return {"action": "SKIP", "reason": "ai_threshold"}

        # SKIP 条件2: diff が margin 未満（拮抗時）
        if diff < margin:
            return {"action": "SKIP", "reason": "ai_margin"}

        # どちらを選ぶか
        if p_buy >= p_sell:
            side = "BUY"
            prob = p_buy  # side側のprob（既存構造に合わせる）
        else:
            side = "SELL"
            prob = p_sell  # side側のprob（既存構造に合わせる）

        return {
            "action": "ENTRY",
            "signal": {
                "side": side,
                "atr_for_lot": probs.get("atr_for_lot"),
                "prob": prob,
            },
            "reason": "entry_ok",
        }


# ============================================================================
# シングルトン関数
# ============================================================================

_ai_service: Optional[AISvc] = None


def get_ai_service() -> AISvc:
    """
    AISvc のシングルトンインスタンスを返す。
    既に作成済みならそれを返し、まだなら初期化してから返す。

    Returns
    -------
    AISvc
        AISvc のシングルトンインスタンス（モデルロードと expected_features 同期済み）
    """
    global _ai_service
    if _ai_service is None:
        _ai_service = AISvc()
    return _ai_service
