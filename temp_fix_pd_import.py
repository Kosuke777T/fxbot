from pathlib import Path
import re

p = Path("app/gui/backtest_tab.py")
s = p.read_text(encoding="utf-8", errors="replace")
orig = s

# 1) ensure global import pandas as pd (once)
if re.search(r'(?m)^import\s+pandas\s+as\s+pd\s*$', s) is None:
    m = re.search(r'(?m)^import\s+numpy\s+as\s+np\s*$', s)
    if m:
        ins = m.end()
        s = s[:ins] + "\nimport pandas as pd" + s[ins:]
    else:
        m2 = re.search(r'(?m)^(?:from|import)\s+.+$', s)
        if not m2:
            raise SystemExit("[NG] cannot find import section")
        ins = m2.end()
        s = s[:ins] + "\nimport pandas as pd" + s[ins:]

# 2) remove any indented local import pandas as pd
s = re.sub(r'(?m)^\s+import\s+pandas\s+as\s+pd\s*\n', '', s)

if s != orig:
    p.write_text(s, encoding="utf-8")
    print("[OK] patched:", p)
else:
    print("[WARN] no changes (maybe already fixed)")

# checks
txt = p.read_text(encoding="utf-8", errors="replace")
print("[CHK] global pandas import:", bool(re.search(r'(?m)^import\s+pandas\s+as\s+pd\s*$', txt)))
print("[CHK] local  pandas import:", bool(re.search(r'(?m)^\s+import\s+pandas\s+as\s+pd\s*$', txt)))

