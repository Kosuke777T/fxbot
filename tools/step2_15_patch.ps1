$ErrorActionPreference = "Stop"

# 一時Pythonパッチ
$pyPath = "tools/tmp_step2_15_patch2.py"
New-Item -ItemType Directory -Force -Path (Split-Path $pyPath) | Out-Null

$py = @'
from pathlib import Path

p = Path("app/gui/scheduler_tab.py")
s = p.read_text(encoding="utf-8", errors="replace")
orig = s

# --- A) _status_icon_sp_from_next_action を安全に丸ごと置換（正規表現なし） ---
needle = "def _status_icon_sp_from_next_action"
start = s.find(needle)
if start < 0:
    raise SystemExit("NG: helper def not found")

# 次のトップレベル def（行頭 def）までを置換対象にする
idx = s.find("\ndef ", start + 1)
end = idx + 1 if idx >= 0 else len(s)

new_block = (
"def _status_icon_sp_from_next_action(next_action):\n"
"    # GUI表示専用: next_action(dict/str) から Statusカード用の標準アイコンを選ぶ（表示のみ）\n"
"    na = next_action\n"
"    if isinstance(na, dict):\n"
"        na = na.get('kind') or na.get('action') or na.get('next_action')\n"
"    if na is None:\n"
"        na = ''\n"
"    na = str(na).upper().strip()\n"
"    if na == 'PROMOTE':\n"
"        return 'SP_DialogApplyButton'\n"
"    if na == 'HOLD':\n"
"        return 'SP_MessageBoxInformation'\n"
"    if na == 'BLOCKED':\n"
"        return 'SP_MessageBoxCritical'\n"
"    return 'SP_MessageBoxQuestion'\n"
"\n"
)

s = s[:start] + new_block + s[end:]
print("OK: helper replaced")

# --- B) Statusカード内 na_row へ「次の一手」リンク風（表示のみ）を追加 ---
if "self.btn_next_action" not in s:
    anchor = "na_row.setSpacing(8)"
    a = s.find(anchor)
    if a < 0:
        # 別のアンカーを試す
        anchor = "na_row = QHBoxLayout()"
        a = s.find(anchor)
        if a < 0:
            print("WARN: na_row not found -> skip link button injection")
        else:
            # na_row 定義の直後、setSpacing の後に挿入
            line_end = s.find("\n", a)
            if line_end >= 0:
                next_line = s.find("\n", line_end + 1)
                if next_line >= 0:
                    # setSpacing 行を探す
                    spacing_line = s.find("na_row.setSpacing", line_end)
                    if spacing_line >= 0:
                        insert_at = s.find("\n", spacing_line) + 1
                        if insert_at > 0:
                            line_start = s.rfind("\n", 0, a) + 1
                            indent = ""
                            for ch in s[line_start:a]:
                                if ch in (" ", "\t"):
                                    indent += ch
                                else:
                                    break

                            ins = (
                                f'{indent}self.btn_next_action = QPushButton("次の一手", self)\n'
                                f'{indent}self.btn_next_action.setObjectName("btn_next_action")\n'
                                f'{indent}self.btn_next_action.setFlat(True)\n'
                                f'{indent}self.btn_next_action.setCursor(Qt.CursorShape.PointingHandCursor)\n'
                                f'{indent}self.btn_next_action.setEnabled(False)\n'
                                f'{indent}self.btn_next_action.setToolTip("表示のみ（将来：next_action 詳細へ誘導）")\n'
                                f'{indent}self.btn_next_action.setStyleSheet("QPushButton#btn_next_action {{ border: none; background: transparent; text-decoration: underline; padding: 0; }}")\n'
                                f'{indent}na_row.addWidget(self.btn_next_action)\n'
                            )

                            s = s[:insert_at] + ins + s[insert_at:]
                            print("OK: link-style next_action button injected (disabled, display-only)")
                        else:
                            print("WARN: could not find insertion point")
                    else:
                        print("WARN: na_row.setSpacing not found")
                else:
                    print("WARN: could not find next line")
            else:
                print("WARN: could not find line end")
    else:
        line_end = s.find("\n", a)
        if line_end < 0:
            line_end = a + len(anchor)
        insert_at = line_end + 1

        line_start = s.rfind("\n", 0, a) + 1
        indent = ""
        for ch in s[line_start:a]:
            if ch in (" ", "\t"):
                indent += ch
            else:
                break

        ins = (
            f'{indent}self.btn_next_action = QPushButton("次の一手", self)\n'
            f'{indent}self.btn_next_action.setObjectName("btn_next_action")\n'
            f'{indent}self.btn_next_action.setFlat(True)\n'
            f'{indent}self.btn_next_action.setCursor(Qt.CursorShape.PointingHandCursor)\n'
            f'{indent}self.btn_next_action.setEnabled(False)\n'
            f'{indent}self.btn_next_action.setToolTip("表示のみ（将来：next_action 詳細へ誘導）")\n'
            f'{indent}self.btn_next_action.setStyleSheet("QPushButton#btn_next_action {{ border: none; background: transparent; text-decoration: underline; padding: 0; }}")\n'
            f'{indent}na_row.addWidget(self.btn_next_action)\n'
        )

        s = s[:insert_at] + ins + s[insert_at:]
        print("OK: link-style next_action button injected (disabled, display-only)")
else:
    print("OK: btn_next_action already exists -> skip")

if s == orig:
    raise SystemExit("NG: no changes applied")

p.write_text(s, encoding="utf-8")
print("OK: wrote", p)
'@

Set-Content -Path $pyPath -Value $py -Encoding UTF8
Write-Host "[OK] wrote python patch => $pyPath"

python -X utf8 $pyPath

Remove-Item $pyPath -Force
Write-Host "[OK] removed temp python patch"

