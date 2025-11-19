# app/services/aisvc_loader.py
import json
from pathlib import Path

ROOT = Path(r"C:\fxbot")  # 運用固定
ACTIVE = ROOT / "active_model.json"
MODELS = ROOT / "models_store"


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
