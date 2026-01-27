# app/services/aisvc_loader.py
import json
from pathlib import Path
from typing import Dict, Any, Optional

ROOT = Path(r"C:\fxbot")  # 運用固定
ACTIVE = ROOT / "active_model.json"
MODELS = ROOT / "models_store"

# 起動時モデル健全性チェック結果のスナップショット（参照窓口）
_MODEL_HEALTH_LAST: Optional[Dict[str, Any]] = None


class ActiveModelInfo(dict):
    @property
    def model_path(self) -> Path:
        return MODELS / self["model_name"]


def load_active_model_meta() -> ActiveModelInfo | None:
    if not ACTIVE.exists():
        return None
    meta = json.loads(ACTIVE.read_text(encoding="utf-8"))
    return ActiveModelInfo(meta)


def resolve_model_path() -> Path | None:
    meta = load_active_model_meta()
    if not meta:
        return None
    p = meta.model_path
    return p if p.exists() else None


# 例：GUI起動時
def load_model_for_inference():
    p = resolve_model_path()
    if not p:
        print("[AISvc] no active model; fallback to bundled default")
        # ここで同梱のデフォルトをロードするなど
        return None
    print(f"[AISvc] loading: {p.name}")
    # 実際は joblib/pickle/onnxruntime 等でロード
    # return joblib.load(p)
    return None


def check_model_health_at_startup(models_dir: str | Path = "models") -> Dict[str, Any]:
    """
    起動時にモデル健全性をチェックする（起動時のみ、tick処理中は呼ばない）。
    
    Returns:
        dict with keys:
            - stable: bool (True=健全, False=問題あり)
            - score: float (100から減点式、比較/表示用)
            - reasons: list[str] (短いコード/短文、問題がある場合のみ)
            - meta: dict (model_path, scaler_path等、可能なら)
    
    例外は絶対に外へ投げない（try/exceptで握って stable=false + reasons に変換）。
    """
    from app.services.ai_service import load_active_model_meta
    
    stable = True
    score = 100.0
    reasons: list[str] = []
    meta: Dict[str, Any] = {}
    
    # 1. active_model.json の存在確認と読み込み
    root = Path(__file__).resolve().parents[2]  # .../app/services -> project root
    active_path = root / "models" / "active_model.json"
    
    try:
        if not active_path.exists():
            stable = False
            score -= 50.0
            reasons.append("active_model_missing")
            return {
                "stable": stable,
                "score": score,
                "reasons": reasons,
                "meta": meta,
            }
        
        # ai_service の load_active_model_meta() を使う（既存API優先）
        meta_dict = load_active_model_meta()
        if not meta_dict:
            stable = False
            score -= 40.0
            reasons.append("active_model_empty")
            meta["active_model_path"] = str(active_path)
            return {
                "stable": stable,
                "score": score,
                "reasons": reasons,
                "meta": meta,
            }
    except json.JSONDecodeError:
        stable = False
        score -= 40.0
        reasons.append("active_model_invalid_json")
        meta["active_model_path"] = str(active_path)
        return {
            "stable": stable,
            "score": score,
            "reasons": reasons,
            "meta": meta,
        }
    except Exception as e:
        stable = False
        score -= 50.0
        reasons.append(f"active_model_read_failed: {type(e).__name__}")
        meta["active_model_path"] = str(active_path)
        return {
            "stable": stable,
            "score": score,
            "reasons": reasons,
            "meta": meta,
        }
    
    # 2. model_path / file の解決
    model_path: Path | None = None
    try:
        
        # model_path を取得（ai_service.AISvc._ensure_model_loaded() のロジックを参考）
        model_path_str = meta_dict.get("model_path")
        if not model_path_str:
            # file から models/<file> を組み立て
            file_name = meta_dict.get("file")
            if file_name:
                root = Path(__file__).resolve().parents[2]
                model_path = root / "models" / file_name
            else:
                stable = False
                score -= 30.0
                reasons.append("model_path_missing")
                meta["active_model_path"] = str(active_path)
                return {
                    "stable": stable,
                    "score": score,
                    "reasons": reasons,
                    "meta": meta,
                }
        else:
            model_path = Path(model_path_str)
        
        if not model_path or not model_path.exists():
            stable = False
            score -= 30.0
            reasons.append("model_file_missing")
            meta["model_path"] = str(model_path) if model_path else "n/a"
            return {
                "stable": stable,
                "score": score,
                "reasons": reasons,
                "meta": meta,
            }
        
        meta["model_path"] = str(model_path)
    except Exception as e:
        stable = False
        score -= 30.0
        reasons.append(f"model_path_resolve_failed: {type(e).__name__}")
        return {
            "stable": stable,
            "score": score,
            "reasons": reasons,
            "meta": meta,
        }
    
    # 3. scaler_path の確認（指定されている場合）
    try:
        scaler_path_str = meta_dict.get("scaler_path")
        if scaler_path_str:
            scaler_path = Path(scaler_path_str)
            if not scaler_path.exists():
                stable = False
                score -= 10.0
                reasons.append("scaler_missing")
            else:
                meta["scaler_path"] = str(scaler_path)
    except Exception as e:
        # scaler は必須ではないので、エラーは無視（reasons に追加しない）
        pass
    
    # 4. モデルの実際のロード（健全性チェック目的で1回だけ）
    try:
        import joblib
        model = joblib.load(model_path)
        
        # predict_proba 等が無いかチェック
        if not (hasattr(model, "predict_proba") or hasattr(model, "predict") or hasattr(model, "decision_function")):
            stable = False
            score -= 20.0
            reasons.append("model_inference_unavailable")
    except Exception as e:
        stable = False
        score -= 20.0
        reasons.append(f"load_failed: {type(e).__name__}")
        return {
            "stable": stable,
            "score": score,
            "reasons": reasons,
            "meta": meta,
        }
    
    # 5. expected_features の検査
    try:
        expected_features = meta_dict.get("expected_features") or meta_dict.get("feature_order") or meta_dict.get("features") or []
        if not expected_features or not isinstance(expected_features, list) or len(expected_features) == 0:
            stable = False
            score -= 15.0
            reasons.append("expected_features_empty")
        else:
            meta["expected_features_count"] = len(expected_features)
    except Exception as e:
        stable = False
        score -= 15.0
        reasons.append(f"expected_features_check_failed: {type(e).__name__}")
    
    result = {
        "stable": stable,
        "score": max(0.0, score),  # 負の値にならないように
        "reasons": reasons,
        "meta": meta,
    }
    
    # 結果をスナップショットとして保存（GUI表示用）
    set_last_model_health(result)
    
    return result


def set_last_model_health(result: Dict[str, Any]) -> None:
    """
    起動時モデル健全性チェック結果を保存する（参照窓口用）。
    
    Args:
        result: check_model_health_at_startup() の戻り値
    """
    global _MODEL_HEALTH_LAST
    try:
        _MODEL_HEALTH_LAST = result.copy() if isinstance(result, dict) else None
    except Exception:
        # 例外は握る（参照窓口なので失敗しても継続）
        _MODEL_HEALTH_LAST = None


def get_last_model_health() -> Optional[Dict[str, Any]]:
    """
    起動時モデル健全性チェック結果を取得する（参照窓口用）。
    
    Returns:
        check_model_health_at_startup() の戻り値、または None（未チェック/失敗時）
    """
    global _MODEL_HEALTH_LAST
    try:
        return _MODEL_HEALTH_LAST.copy() if _MODEL_HEALTH_LAST is not None else None
    except Exception:
        # 例外は握る（参照窓口なので失敗しても継続）
        return None
