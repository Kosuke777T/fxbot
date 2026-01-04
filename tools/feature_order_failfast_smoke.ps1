param(
  [string]$Symbol = "USDJPY-"
)
$ErrorActionPreference="Stop"

# 1) active_model.json の expected_features を読み、先頭2つをswapした order を作る
python -X utf8 -c @"
import json
from pathlib import Path

cands = [Path('config/active_model.json'), Path('configs/active_model.json'), Path('active_model.json'), Path('models/active_model.json')]
p = next((x for x in cands if x.exists()), None)
if p is None:
    raise SystemExit('[NG] active_model.json not found')

meta = json.loads(p.read_text(encoding='utf-8'))
exp = meta.get('expected_features') or meta.get('feature_order') or meta.get('features') or []
if not exp:
    raise SystemExit('[NG] expected_features empty in active_model.json (see validate_feature_order_fail_fast hint)')

bad = list(exp)
if len(bad) >= 2:
    bad[0], bad[1] = bad[1], bad[0]

print('[INFO] expected_features_n=', len(exp))
print('[INFO] bad_feature_order_head=', bad[:5])

from app.services.ai_service import validate_feature_order_fail_fast
try:
    validate_feature_order_fail_fast(bad, context='backtest')
    print('[NG] should have failed but passed')
    raise SystemExit(2)
except RuntimeError as e:
    print('[OK] fail-fast triggered')
    print(str(e))
"@
