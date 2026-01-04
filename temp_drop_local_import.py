from pathlib import Path
import re

p = Path("app/gui/backtest_tab.py")
s = p.read_text(encoding="utf-8", errors="replace")

s2 = re.sub(r'(?m)^\s+import pandas as pd\s*\n', '', s, count=1)

if s2 == s:
    raise SystemExit("[NG] target line not found (import pandas as pd). File may have changed.")

p.write_text(s2, encoding="utf-8")
print("[OK] removed local 'import pandas as pd' inside _load_plot: app/gui/backtest_tab.py")

