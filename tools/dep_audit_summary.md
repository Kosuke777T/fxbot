# 依存境界違反 監査サマリ（観測結果のみ・0変更）

観測日: rg による `^from app\.(services|core)|^import app\.(services|core)` の一致箇所を根拠とする。

## 1) core → services（最優先: 循環・テスト困難化の主因）

| ファイル | 行 | import文 | なぜ危険か |
|----------|-----|----------|------------|
| app/core/backtest/backtest_engine.py | 56 | `from app.services.filter_service import evaluate_entry` | core（ドメイン/バックテスト）が services に依存→循環・単体テストでサービス層を差し替えられない。 |
| app/core/backtest/backtest_engine.py | 57 | `from app.services.profile_stats_service import get_profile_stats_service` | 同上。責務境界違反でバックテストが実行時インフラに縛られる。 |

**件数: 2 件（1 ファイル）**

---

## 2) gui → core（責務境界の崩壊）

`tools/dep_audit_core_imports.txt` のうち、パスが `app/gui` で始まる行の一覧（観測ベース）。

| ファイル | 行 | import文 |
|----------|-----|----------|
| app/gui/control_tab.py | 25 | `from app.core.config_loader import load_config` |
| app/gui/ai_tab.py | 30 | `from app.core.strategy_profile import get_profile` |
| app/gui/main.py | 18 | `from app.core import logger as app_logger` |
| app/gui/main.py | 36 | `from app.core.config_loader import load_config` |
| app/gui/main.py | 37 | `from app.core import market` |
| app/gui/widgets/monthly_returns_widget.py | 12 | `from app.core.strategy_profile import get_profile` |

**件数: 6 行（4 ファイル）**

---

## 3) services → core（逆依存: 許容される場合あり・列挙のみ）

`tools/dep_audit_core_imports.txt` のうち、パスが `app/services` で始まる行。一覧は同ファイル参照。件数: 22 行（複数ファイル）。

---

## 参照ファイル

- **app.services を import している箇所（core / gui / services 内）**: `tools/dep_audit_services_imports.txt`
- **app.core を import している箇所（gui / services 内）**: `tools/dep_audit_core_imports.txt`
