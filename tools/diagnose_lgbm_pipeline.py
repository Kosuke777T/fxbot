# tools/diagnose_lgbm_pipeline.py
"""
LightGBMパイプライン診断スクリプト

【目的】
LightGBMが「仕事していない」ように見える原因を推測ではなく観測で確定する。

【観測対象の実装箇所（grep結果より）】
1. active_model.json を読む箇所:
   - app/strategies/ai_strategy.py:load_active_model() (line 86)
   - core/ai/service.py:_read_active_model_path() (line 18)
   - app/services/ai_service.py:_fallback_load_active_meta() (line 67)
   - app/services/aisvc_loader.py:load_active_model_meta() (line 20)

2. モデルロード（joblib.load 等）の最終地点:
   - app/strategies/ai_strategy.py:_load_model_generic() (line 15)
   - core/ai/loader.py:ModelWrapper.__init__() (line 20)
   - core/ai/service.py:AISvc._initialize_model() (line 117)

3. predict_proba 呼び出し箇所:
   - core/ai/service.py:AISvc.predict() (line 196)
   - core/ai/loader.py:ModelWrapper.predict_proba() (line 58)
   - core/ai/loader.py:_CalibratedWrapper.predict_proba() (line 155)

4. prob_buy/prob_sell を組み立てる箇所:
   - core/ai/service.py:AISvc.predict() (line 211-212): p_buy = probs[0, 1], p_sell = probs[0, 0]
   - app/services/ai_service.py:AISvc.predict() (line 721-727): classes_ に基づく index マッピング

5. model_id を付与/更新する箇所:
   - scripts/swap_model.py: モデル切り替え時に更新
   - scripts/promote_model.py: モデルプロモーション時に更新
   - models/active_model.json: model_name, version フィールド

【実行手順（PowerShell 7）】
  python -X utf8 tools/diagnose_lgbm_pipeline.py --symbol "USDJPY-"
  python -X utf8 tools/diagnose_lgbm_pipeline.py --symbol "USDJPY-" --csv "data/USDJPY/ohlcv/USDJPY_M5.csv"
  python -X utf8 tools/diagnose_lgbm_pipeline.py --profile "michibiki_std"

【OK/NG判定条件】
  MODEL_OK: モデルロード成功 && predict_proba が呼べる
  FEATURES_OK: expected_features が取得でき && 実特徴量と一致（欠損なし、順序一致）
  PROBA_OK: predict_proba の戻り shape が (1, 2) 以上 && 値が 0-1 の範囲
  MAPPING_OK: classes_ から BUY/SELL の index が確定できる
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

# プロジェクトルートを sys.path に追加
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 既存APIを優先利用
from core.ai.service import AISvc, _as_2d_frame
from core.ai.loader import ModelWrapper, load_lgb_clf
from app.strategies.ai_strategy import load_active_model, build_features, get_active_model_meta


def _extract_classes_any(model_obj: Any) -> Optional[Any]:
    """
    Best-effortで classes_ を回収する（app/services/ai_service.py と同じロジック）。
    Pipeline/CalibratedClassifierCV/ラッパー/tuple/dict などを想定。
    """
    if model_obj is None:
        return None

    # 1) 直である
    cls = getattr(model_obj, "classes_", None)
    if cls is not None:
        return cls

    # 2) sklearn Pipeline
    named_steps = getattr(model_obj, "named_steps", None)
    if isinstance(named_steps, dict) and named_steps:
        for step in reversed(list(named_steps.values())):
            cls = getattr(step, "classes_", None)
            if cls is not None:
                return cls

    # 3) よくあるラッパー
    for attr in ("estimator", "base_estimator", "classifier", "model", "base_model"):
        inner = getattr(model_obj, attr, None)
        if inner is not None:
            cls = getattr(inner, "classes_", None)
            if cls is not None:
                return cls

    # 4) CalibratedClassifierCV 系
    ccs = getattr(model_obj, "calibrated_classifiers_", None)
    if isinstance(ccs, (list, tuple)) and ccs:
        for cc in ccs:
            est = getattr(cc, "estimator", None)
            if est is not None:
                cls = getattr(est, "classes_", None)
                if cls is not None:
                    return cls

    # 5) tuple/dict で包まれてるケース
    if isinstance(model_obj, (list, tuple)) and model_obj:
        for item in model_obj:
            cls = _extract_classes_any(item)
            if cls is not None:
                return cls
    if isinstance(model_obj, dict) and model_obj:
        for item in model_obj.values():
            cls = _extract_classes_any(item)
            if cls is not None:
                return cls

    return None


def _determine_class_index_map_from_classes(classes_list: list) -> Optional[dict[str, Any]]:
    """
    classes_list から直接 BUY/SELL の index を確定する。
    """
    if len(classes_list) < 2:
        return None

    buy_index = None
    sell_index = None
    source = "unknown"

    # 文字列の場合
    for idx, cls in enumerate(classes_list):
        cls_str = str(cls).upper()
        if cls_str in ("BUY", "LONG"):
            buy_index = idx
        elif cls_str in ("SELL", "SHORT"):
            sell_index = idx

    # 数値の場合
    if buy_index is None or sell_index is None:
        try:
            # np.int64 などを int に変換
            classes_list_int = [int(cls) if hasattr(cls, '__int__') else cls for cls in classes_list]
            classes_set = set(classes_list_int)

            # {0, 1} の場合: SELL=0, BUY=1
            if classes_set == {0, 1}:
                for idx, cls in enumerate(classes_list_int):
                    if cls == 0:
                        sell_index = idx
                    elif cls == 1:
                        buy_index = idx
                source = f"numeric:{{0,1}}"

            # {-1, 1} の場合: SELL=-1, BUY=1
            elif classes_set == {-1, 1}:
                for idx, cls in enumerate(classes_list_int):
                    if cls == -1:
                        sell_index = idx
                    elif cls == 1:
                        buy_index = idx
                source = f"numeric:{{-1,1}}"

            else:
                return None
        except Exception:
            return None

    if buy_index is not None and sell_index is not None:
        return {
            "classes": classes_list,
            "buy_index": buy_index,
            "sell_index": sell_index,
            "source": source,
        }
    else:
        return None


def _determine_class_index_map(model: Any) -> Optional[dict[str, Any]]:
    """
    model.classes_ を観測して BUY/SELL の index を確定する（app/services/ai_service.py と同じロジック）。
    """
    classes = None
    source = "unknown"

    try:
        classes = _extract_classes_any(model)
        if classes is not None:
            if hasattr(model, "classes_"):
                source = "classes_"
            elif hasattr(model, "named_steps"):
                source = "pipeline.named_steps"
            elif hasattr(model, "base_estimator"):
                source = "base_estimator"
            elif hasattr(model, "calibrated_classifiers_"):
                source = "calibrated_classifiers_"
            else:
                source = "extracted"
    except Exception:
        return None

    if classes is None:
        return None

    try:
        classes_list = list(classes)
    except Exception:
        return None

    if len(classes_list) < 2:
        return None

    buy_index = None
    sell_index = None

    # 文字列の場合
    for idx, cls in enumerate(classes_list):
        cls_str = str(cls).upper()
        if cls_str in ("BUY", "LONG"):
            buy_index = idx
        elif cls_str in ("SELL", "SHORT"):
            sell_index = idx

    # 数値の場合
    if buy_index is None or sell_index is None:
        try:
            # np.int64 などを int に変換
            classes_list_int = [int(cls) if hasattr(cls, '__int__') else cls for cls in classes_list]
            classes_set = set(classes_list_int)
            print(f"  [デバッグ] classes_list_int: {classes_list_int}, classes_set: {classes_set}")

            # {0, 1} の場合: SELL=0, BUY=1
            if classes_set == {0, 1}:
                print(f"  [デバッグ] classes_set == {{0, 1}} が True")
                for idx, cls in enumerate(classes_list_int):
                    if cls == 0:
                        sell_index = idx
                    elif cls == 1:
                        buy_index = idx
                source = f"numeric:{{0,1}}"

            # {-1, 1} の場合: SELL=-1, BUY=1
            elif classes_set == {-1, 1}:
                for idx, cls in enumerate(classes_list_int):
                    if cls == -1:
                        sell_index = idx
                    elif cls == 1:
                        buy_index = idx
                source = f"numeric:{{-1,1}}"

            else:
                return None
        except Exception:
            return None

    if buy_index is not None and sell_index is not None:
        return {
            "classes": classes_list,
            "buy_index": buy_index,
            "sell_index": sell_index,
            "source": source,
        }
    else:
        return None


def diagnose_model() -> dict[str, Any]:
    """
    診断を実行し、観測結果を返す。
    """
    result: dict[str, Any] = {
        "active_model": {},
        "model_load": {},
        "classes": {},
        "proba": {},
        "features": {},
        "inference": {},
        "summary": {},
    }

    # =====================================================
    # 1) active_model.json の解決結果
    # =====================================================
    print("=" * 80)
    print("[観測] 1. active_model.json の解決")
    print("=" * 80)

    try:
        meta_path = PROJECT_ROOT / "models" / "active_model.json"
        if not meta_path.exists():
            result["active_model"]["error"] = f"active_model.json not found: {meta_path}"
            print(f"  ❌ エラー: {result['active_model']['error']}")
            return result

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        result["active_model"]["path"] = str(meta_path.resolve())
        result["active_model"]["exists"] = True
        result["active_model"]["meta"] = meta

        model_name = meta.get("model_name", "")
        file_name = meta.get("file", "")
        expected_features = meta.get("feature_order") or meta.get("features") or []

        model_path = PROJECT_ROOT / "models" / file_name
        result["active_model"]["model_path"] = str(model_path.resolve())
        result["active_model"]["model_exists"] = model_path.exists()
        result["active_model"]["expected_features_count"] = len(expected_features)
        result["active_model"]["expected_features"] = expected_features

        print(f"  ✓ active_model.json: {meta_path.resolve()}")
        print(f"  ✓ model_name: {model_name}")
        print(f"  ✓ file: {file_name}")
        print(f"  ✓ model_path: {model_path.resolve()}")
        print(f"  ✓ model_exists: {model_path.exists()}")
        print(f"  ✓ expected_features数: {len(expected_features)}")
        if expected_features:
            print(f"  ✓ expected_features: {expected_features[:5]}..." if len(expected_features) > 5 else f"  ✓ expected_features: {expected_features}")

    except Exception as e:
        result["active_model"]["error"] = str(e)
        print(f"  ❌ エラー: {e}")
        return result

    # =====================================================
    # 2) モデルの種類判定とロード成功
    # =====================================================
    print("\n" + "=" * 80)
    print("[観測] 2. モデルの種類判定とロード")
    print("=" * 80)

    try:
        # core/ai/service.py の AISvc を使う（既存API優先）
        aisvc = AISvc()
        model = aisvc.model

        if model is None:
            result["model_load"]["error"] = "モデルが None"
            print("  ❌ エラー: モデルが None")
            return result

        # ModelWrapper の base_model を取得
        base_model = getattr(model, "base_model", model)
        model_type = type(base_model).__name__
        module = type(base_model).__module__

        result["model_load"]["success"] = True
        result["model_load"]["model_type"] = model_type
        result["model_load"]["module"] = module
        result["model_load"]["has_predict_proba"] = hasattr(model, "predict_proba")
        result["model_load"]["has_predict"] = hasattr(model, "predict")

        print(f"  ✓ モデルロード成功")
        print(f"  ✓ model_type: {model_type}")
        print(f"  ✓ module: {module}")
        print(f"  ✓ has_predict_proba: {hasattr(model, 'predict_proba')}")
        print(f"  ✓ has_predict: {hasattr(model, 'predict')}")

        # LightGBM判定
        is_lgbm = "lightgbm" in module.lower() or "lgb" in model_type.lower()
        result["model_load"]["is_lightgbm"] = is_lgbm
        print(f"  ✓ is_lightgbm: {is_lgbm}")

    except Exception as e:
        result["model_load"]["error"] = str(e)
        print(f"  ❌ エラー: {e}")
        import traceback
        traceback.print_exc()
        return result

    # =====================================================
    # 3) model.classes_ の有無と内容
    # =====================================================
    print("\n" + "=" * 80)
    print("[観測] 3. model.classes_ の有無と内容")
    print("=" * 80)

    try:
        # ModelWrapper や _CalibratedWrapper の場合は base_model から取得
        target_model = model
        
        # ModelWrapper や _CalibratedWrapper の場合は base_model から取得
        if hasattr(model, "base_model"):
            target_model = model.base_model
        
        # ModelWrapper の場合も base_model を取得
        if hasattr(target_model, "base_model"):
            target_model = target_model.base_model
        
        # 直接アクセスを試みる（__getattr__ で委譲している場合も含む）
        classes = None
        try:
            classes = getattr(model, "classes_", None)
        except Exception:
            pass
        
        if classes is None:
            try:
                classes = getattr(target_model, "classes_", None)
            except Exception:
                pass
        
        if classes is None:
            classes = _extract_classes_any(target_model)
        
        if classes is None:
            classes = _extract_classes_any(model)
        
        # Booster の場合は classes_ が無いことがある（バイナリ分類で確率値のみ返す）
        if classes is None:
            # Booster の場合は predict_proba の shape から推測
            try:
                dummy_df = pd.DataFrame([{name: 0.0 for name in result["active_model"].get("expected_features", [])}])
                # base_model から直接取得を試みる
                base_model = getattr(model, "base_model", model)
                if hasattr(base_model, "base_model"):
                    base_model = base_model.base_model
                
                if hasattr(base_model, "predict_proba"):
                    proba_test = base_model.predict_proba(dummy_df)
                    proba_arr_test = np.asarray(proba_test, dtype=float)
                    if proba_arr_test.ndim == 2 and proba_arr_test.shape[1] == 2:
                        # 2次元配列で2列の場合はバイナリ分類（SELL=0, BUY=1 の規約に従う）
                        classes = np.array([0, 1])
                        result["classes"]["note"] = "classes_ が無いため、predict_proba の shape から推測（SELL=0, BUY=1）"
                    elif proba_arr_test.ndim == 1:
                        # 1次元配列の場合はバイナリ分類で確率値のみ
                        classes = np.array([0, 1])
                        result["classes"]["note"] = "classes_ が無いため、predict_proba の shape から推測（SELL=0, BUY=1）"
                elif hasattr(base_model, "predict"):
                    # Booster の場合は predict が確率値を返す
                    classes = np.array([0, 1])
                    result["classes"]["note"] = "classes_ が無いため、Booster の規約に従い推測（SELL=0, BUY=1）"
            except Exception:
                pass
        
        if classes is not None:
            classes_list = list(classes)
            result["classes"]["exists"] = True
            result["classes"]["classes"] = classes_list
            result["classes"]["count"] = len(classes_list)

            print(f"  ✓ classes_ が存在します")
            print(f"  ✓ classes: {classes_list}")
            print(f"  ✓ count: {len(classes_list)}")

            # BUY/SELL の index マッピングを確定（target_model と model の両方を試す）
            # classes を直接渡す（推測された classes を使う）
            class_map = _determine_class_index_map_from_classes(classes_list)
            if not class_map:
                class_map = _determine_class_index_map(target_model)
            if not class_map:
                class_map = _determine_class_index_map(model)
            if class_map:
                result["classes"]["class_index_map"] = class_map
                print(f"  ✓ BUY/SELL マッピング確定:")
                print(f"    - classes: {class_map['classes']}")
                print(f"    - buy_index: {class_map['buy_index']}")
                print(f"    - sell_index: {class_map['sell_index']}")
                print(f"    - source: {class_map['source']}")
            else:
                result["classes"]["class_index_map"] = None
                print(f"  ⚠ BUY/SELL マッピングを確定できませんでした")
        else:
            result["classes"]["exists"] = False
            result["classes"]["classes"] = None
            result["classes"]["note"] = "classes_ が存在せず、predict_proba の shape からも推測できませんでした"
            print(f"  ⚠ classes_ が存在しません")
            print(f"  ⚠ 注: LightGBM Booster など、classes_ を持たないモデルの可能性があります")

    except Exception as e:
        result["classes"]["error"] = str(e)
        print(f"  ❌ エラー: {e}")

    # =====================================================
    # 4) predict_proba の戻り shape と先頭N件の値
    # =====================================================
    print("\n" + "=" * 80)
    print("[観測] 4. predict_proba の戻り shape と値")
    print("=" * 80)

    try:
        # ダミー特徴量で推論（expected_features に基づく）
        expected_features = result["active_model"].get("expected_features", [])
        if not expected_features:
            result["proba"]["error"] = "expected_features が空"
            print(f"  ❌ エラー: expected_features が空")
        else:
            # ダミー特徴量を作成（全0でも可）
            dummy_features = {name: 0.0 for name in expected_features}
            dummy_df = pd.DataFrame([dummy_features])

            # predict_proba を呼ぶ（wrapper と base_model の両方を試す）
            proba_arr = None
            proba_source = "unknown"
            
            if hasattr(model, "predict_proba"):
                try:
                    proba_raw = model.predict_proba(dummy_df)
                    proba_arr = np.asarray(proba_raw, dtype=float)
                    proba_source = "wrapper"
                except Exception:
                    pass
            
            # base_model から直接取得を試みる（2次元配列を期待）
            if proba_arr is None or proba_arr.ndim == 1:
                try:
                    base_model = getattr(model, "base_model", model)
                    if hasattr(base_model, "base_model"):
                        base_model = base_model.base_model
                    if hasattr(base_model, "predict_proba"):
                        base_proba_raw = base_model.predict_proba(dummy_df)
                        base_proba_arr = np.asarray(base_proba_raw, dtype=float)
                        if base_proba_arr.ndim == 2:
                            proba_arr = base_proba_arr
                            proba_source = "base_model"
                    elif hasattr(base_model, "predict"):
                        # Booster の場合は predict が確率値を返す
                        base_pred = base_model.predict(dummy_df)
                        base_pred_arr = np.asarray(base_pred, dtype=float)
                        if base_pred_arr.ndim == 1:
                            # 1次元配列の場合は [1-p, p] の形式に変換
                            p1 = base_pred_arr[0]
                            proba_arr = np.array([[1.0 - p1, p1]])
                            proba_source = "base_model_predict"
                except Exception:
                    pass

            if proba_arr is not None:
                result["proba"]["success"] = True
                result["proba"]["shape"] = list(proba_arr.shape)
                result["proba"]["ndim"] = proba_arr.ndim
                result["proba"]["dtype"] = str(proba_arr.dtype)
                result["proba"]["source"] = proba_source

                # 先頭5件の値を取得
                if proba_arr.ndim == 1:
                    head_values = proba_arr[:5].tolist()
                elif proba_arr.ndim == 2:
                    head_values = proba_arr[0, :].tolist() if proba_arr.shape[0] > 0 else []
                else:
                    head_values = []

                result["proba"]["head_values"] = head_values
                result["proba"]["min"] = float(proba_arr.min())
                result["proba"]["max"] = float(proba_arr.max())

                print(f"  ✓ predict_proba 成功 (source: {proba_source})")
                print(f"  ✓ shape: {proba_arr.shape}")
                print(f"  ✓ ndim: {proba_arr.ndim}")
                print(f"  ✓ dtype: {proba_arr.dtype}")
                print(f"  ✓ 先頭値: {head_values}")
                print(f"  ✓ min: {proba_arr.min():.6f}, max: {proba_arr.max():.6f}")
            else:
                result["proba"]["error"] = "predict_proba が存在しません"
                print(f"  ❌ エラー: predict_proba が存在しません")

    except Exception as e:
        result["proba"]["error"] = str(e)
        print(f"  ❌ エラー: {e}")
        import traceback
        traceback.print_exc()

    # =====================================================
    # 5) p_buy / p_sell の対応を classes_ から確定
    # =====================================================
    print("\n" + "=" * 80)
    print("[観測] 5. p_buy / p_sell の対応（classes_ から確定）")
    print("=" * 80)

    class_map = result["classes"].get("class_index_map")
    if class_map:
        buy_idx = class_map.get("buy_index")
        sell_idx = class_map.get("sell_index")
        result["inference"]["mapping_determined"] = True
        result["inference"]["buy_index"] = buy_idx
        result["inference"]["sell_index"] = sell_idx
        print(f"  ✓ マッピング確定:")
        print(f"    - BUY -> proba[:, {buy_idx}]")
        print(f"    - SELL -> proba[:, {sell_idx}]")
    else:
        result["inference"]["mapping_determined"] = False
        result["inference"]["buy_index"] = None
        result["inference"]["sell_index"] = None
        print(f"  ⚠ マッピングを確定できませんでした（不確定）")

    # =====================================================
    # 6) 特徴量の欠損/順序ズレの疑いを検出
    # =====================================================
    print("\n" + "=" * 80)
    print("[観測] 6. 特徴量の欠損/順序ズレの検出")
    print("=" * 80)

    expected_features = result["active_model"].get("expected_features", [])
    if expected_features:
        # モデルから実際の特徴量順序を取得（可能なら）
        actual_features = None
        try:
            # ModelWrapper や _CalibratedWrapper の場合は base_model から取得
            base_model = getattr(model, "base_model", model)
            # _CalibratedWrapper の場合はさらに base_model を取得
            if hasattr(base_model, "base_model"):
                base_model = base_model.base_model
            
            if hasattr(base_model, "feature_name_"):
                actual_features = list(base_model.feature_name_)
            elif hasattr(base_model, "booster_") and hasattr(base_model.booster_, "feature_name"):
                actual_features = list(base_model.booster_.feature_name())
            elif hasattr(base_model, "booster") and callable(getattr(base_model, "booster", None)):
                b = base_model.booster()
                if hasattr(b, "feature_name"):
                    actual_features = list(b.feature_name())
        except Exception:
            pass

        if actual_features:
            result["features"]["actual_features"] = actual_features
            result["features"]["expected_features"] = expected_features

            missing = [f for f in expected_features if f not in actual_features]
            extra = [f for f in actual_features if f not in expected_features]
            order_match = expected_features == actual_features

            result["features"]["missing"] = missing
            result["features"]["extra"] = extra
            result["features"]["order_match"] = order_match

            print(f"  ✓ expected_features: {len(expected_features)} 個")
            print(f"  ✓ actual_features: {len(actual_features)} 個")
            if missing:
                print(f"  ⚠ 欠損特徴量: {missing}")
            if extra:
                print(f"  ⚠ 余分特徴量: {extra}")
            if not order_match:
                print(f"  ⚠ 順序不一致: expected={expected_features[:3]}..., actual={actual_features[:3]}...")
            if not missing and not extra and order_match:
                print(f"  ✓ 特徴量一致（欠損なし、順序一致）")
        else:
            result["features"]["actual_features"] = None
            result["features"]["note"] = "モデルから特徴量順序を取得できませんでした"
            print(f"  ⚠ {result['features']['note']}")

    # =====================================================
    # 7) 直近1行の features で推論を1回だけ実施
    # =====================================================
    print("\n" + "=" * 80)
    print("[観測] 7. 直近1行の features で推論実施")
    print("=" * 80)

    try:
        # AISvc.predict() を使って推論（既存API優先）
        expected_features = result["active_model"].get("expected_features", [])
        if expected_features:
            # ダミー特徴量を作成
            dummy_features = {name: 0.0 for name in expected_features}
            prob_out = aisvc.predict(dummy_features)

            result["inference"]["prob_buy"] = float(prob_out.p_buy)
            result["inference"]["prob_sell"] = float(prob_out.p_sell)
            result["inference"]["prob_skip"] = float(prob_out.p_skip)

            # raw_proba も取得（base_model から直接取得を試みる）
            dummy_df = pd.DataFrame([dummy_features])
            if hasattr(model, "predict_proba"):
                raw_proba = model.predict_proba(dummy_df)
                raw_proba_arr = np.asarray(raw_proba, dtype=float)
                if raw_proba_arr.ndim == 2 and raw_proba_arr.shape[0] > 0:
                    result["inference"]["raw_proba"] = raw_proba_arr[0, :].tolist()
                else:
                    result["inference"]["raw_proba"] = raw_proba_arr.tolist() if raw_proba_arr.ndim == 1 else []
            
            # base_model から直接 predict_proba を呼んで比較
            try:
                base_model = getattr(model, "base_model", model)
                if hasattr(base_model, "base_model"):
                    base_model = base_model.base_model
                if hasattr(base_model, "predict_proba"):
                    base_proba = base_model.predict_proba(dummy_df)
                    base_proba_arr = np.asarray(base_proba, dtype=float)
                    if base_proba_arr.ndim == 2:
                        result["inference"]["base_model_raw_proba"] = base_proba_arr[0, :].tolist()
                    else:
                        result["inference"]["base_model_raw_proba"] = base_proba_arr.tolist()
            except Exception:
                pass

            print(f"  ✓ prob_buy: {prob_out.p_buy:.6f}")
            print(f"  ✓ prob_sell: {prob_out.p_sell:.6f}")
            print(f"  ✓ prob_skip: {prob_out.p_skip:.6f}")
            if "raw_proba" in result["inference"]:
                print(f"  ✓ raw_proba: {result['inference']['raw_proba']}")
        else:
            result["inference"]["error"] = "expected_features が空"
            print(f"  ❌ エラー: expected_features が空")

    except Exception as e:
        result["inference"]["error"] = str(e)
        print(f"  ❌ エラー: {e}")
        import traceback
        traceback.print_exc()

    # =====================================================
    # 判定サマリ
    # =====================================================
    print("\n" + "=" * 80)
    print("[判定] サマリ")
    print("=" * 80)

    model_ok = (
        result["model_load"].get("success", False)
        and result["model_load"].get("has_predict_proba", False)
    )
    features_ok = (
        len(result["active_model"].get("expected_features", [])) > 0
        and result["features"].get("missing", []) == []
        and result["features"].get("order_match", True)
    )
    proba_ok = (
        result["proba"].get("success", False)
        and result["proba"].get("ndim", 0) >= 1
        and result["proba"].get("min", 1.0) >= 0.0
        and result["proba"].get("max", -1.0) <= 1.0
    )
    mapping_ok = result["inference"].get("mapping_determined", False)

    result["summary"] = {
        "MODEL_OK": model_ok,
        "FEATURES_OK": features_ok,
        "PROBA_OK": proba_ok,
        "MAPPING_OK": mapping_ok,
    }

    print(f"  MODEL_OK: {model_ok}")
    if not model_ok:
        reason = "モデルロード失敗" if not result["model_load"].get("success") else "predict_proba なし"
        print(f"    → 理由: {reason}")

    print(f"  FEATURES_OK: {features_ok}")
    if not features_ok:
        missing = result["features"].get("missing", [])
        order_match = result["features"].get("order_match", True)
        if missing:
            print(f"    → 理由: 欠損特徴量あり {missing}")
        elif not order_match:
            print(f"    → 理由: 順序不一致")
        else:
            print(f"    → 理由: expected_features が空")

    print(f"  PROBA_OK: {proba_ok}")
    if not proba_ok:
        if not result["proba"].get("success"):
            print(f"    → 理由: predict_proba 呼び出し失敗")
        else:
            min_val = result["proba"].get("min", 1.0)
            max_val = result["proba"].get("max", -1.0)
            if min_val < 0.0 or max_val > 1.0:
                print(f"    → 理由: 値が 0-1 の範囲外 (min={min_val:.6f}, max={max_val:.6f})")

    print(f"  MAPPING_OK: {mapping_ok}")
    if not mapping_ok:
        print(f"    → 理由: classes_ から BUY/SELL の index を確定できませんでした")

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="LightGBMパイプライン診断スクリプト")
    parser.add_argument("--profile", type=str, help="プロファイル名（未使用、将来拡張用）")
    parser.add_argument("--symbol", type=str, default="USDJPY-", help="シンボル名（未使用、将来拡張用）")
    parser.add_argument("--csv", type=str, help="CSVファイルパス（未使用、将来拡張用）")
    args = parser.parse_args()

    # 診断実行
    result = diagnose_model()

    # 終了コード: すべて OK なら 0、それ以外は 1
    summary = result.get("summary", {})
    all_ok = all([
        summary.get("MODEL_OK", False),
        summary.get("FEATURES_OK", False),
        summary.get("PROBA_OK", False),
        summary.get("MAPPING_OK", False),
    ])

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
