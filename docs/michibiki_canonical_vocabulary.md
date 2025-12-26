# ミチビキ 公式語彙（Canonical Vocabulary）

目的：ミチビキ内部で「同じ意味を、同じ言葉で、同じ粒度で」扱う。
原則：1概念=1語彙。判断に使う語彙だけを公式化。表示の言い換えはGUI側で行う。

---

## 0. 基本原則
- 同じ概念に複数の名前を付けない
- servicesは公式語彙のみを出力する（GUIは表示用に言い換えてよい）
- 判断に使う語彙は enum / 段階（LOW/MID/HIGH等）を優先し、数値は補助とする
- すべての判断には reason を付与する
- ログに残らない判断は存在しない

---

## 1. Ops Core（最終判断）

### next_action
意味：ミチビキとしての最終判断（売買アクションではない）
型：enum
- PROMOTE：前進してよい
- HOLD：状況待ち
- BLOCKED：実行してはいけない（守り優先）

---

## 2. 安定性（Truth Layer）

### stable
意味：信用できる状態か
型：bool

### score
意味：安定性の強さ（比較・説明用）
型：float（0–100）

### reasons
意味：stable=false の理由
型：string[]

---

## 3. 条件探索（T-43）

### condition
意味：評価対象となる判断条件のまとまり（ID付き）
型：object（ID必須）

### support
意味：condition成立回数
型：int

### condition_confidence
意味：条件が今も信用できる度合い（段階化）
型：enum
- LOW | MID | HIGH

### degradation
意味：最近の劣化兆候
型：bool
- true：最近効かなくなっている兆候あり（攻めではブレーキ）

---

## 4. 利益設計（T-44）

### upside_potential
意味：当たった場合の伸びしろ（過去分布ベース）
型：enum
- LOW | MID | HIGH

### profit_health
意味：最近の利益状態の健全さ
型：enum
- GOOD | WARNING | BAD

### exit_type
意味：EXITの性質
型：enum
- DEFENSE | PROFIT

### exit_reason
意味：EXITした直接理由（短いコード/短文）
型：string

### size_decision
意味：サイズ変更の判断（説明必須）
型：object
例：
```yaml
size_decision:
  multiplier: 1.0
  reason: "stable_high_confidence"

5. 自動化統治（T-45）
automation_level

意味：自動化段階
型：int

0：提案のみ

1：守りのみ自動

2：限定攻め

3：FULL-AUTO（封印中）

action

意味：実行を検討する操作単位
型：enum

EXEC_ENTRY

EXEC_EXIT_DEFENSE

EXEC_EXIT_PROFIT

SIZE_UP

SIZE_DOWN

PROMOTE_APPLY

authorize

意味：自動化判定の結果（reason必須）
型：object
例：

authorize:
  allowed: true
  reason: "stable_and_supported"

proposal

意味：人間承認が必要な提案（実行と区別する）
型：object（action/params/reasons/evidenceを含む）

6. 監査・説明（Audit）
evidence

意味：判断の根拠スナップショット
型：object（stability/condition/profit/governanceの主要キーを含む）

policy_hash

意味：この判断に使われたポリシー識別子（再現性のため必須）
型：string

7. 絶対ルール（運用）

enum/段階を公式判断値とする（数値は補助）

すべての判断に reason を付ける

監査ログ無しの自動実行は禁止

GUIはFacadeのみを呼び、servicesは公式語彙で返す
