#!/usr/bin/env python
"""
EditionGuard v5.1 動作確認スクリプト

使用方法:
  python test_edition_guard.py
"""

from app.services.edition_guard import (
    EditionGuard,
    get_capability,
    allow_real_account,
    scheduler_limit,
    filter_level,
    ranking_level,
    current_edition,
)


def print_caps(label: str):
    """指定されたエディションの CapabilitySet を表示"""
    g = EditionGuard(label)
    caps = g.capabilities
    print(f"=== {label} ===")
    print(" edition:", g.edition)
    print(" fi_level:", caps.fi_level)
    print(" shap_level:", caps.shap_level)
    print(" scheduler_level:", caps.scheduler_level, "limit:", g.scheduler_limit())
    print(" filter_level:", caps.filter_level)
    print(" ranking_level:", caps.ranking_level)
    print(" allow_real_account:", g.allow_real_account())
    print()


def main():
    """メイン処理"""
    # 1) 個別にエディションを指定して確認
    for ed in ["free", "basic", "pro", "expert", "master", "unknown"]:
        print_caps(ed)

    # 2) config/edition.json or 環境変数から自動判定されるデフォルトの確認
    print("=== default (config/env) ===")
    print(" current_edition():", current_edition())
    print(" fi_level:", get_capability("fi_level"))
    print(" shap_level:", get_capability("shap_level"))
    print(" scheduler_level:", get_capability("scheduler_level"))
    print(" scheduler_limit:", scheduler_limit())
    print(" filter_level:", filter_level())
    print(" ranking_level:", ranking_level())
    print(" allow_real_account:", allow_real_account())


if __name__ == "__main__":
    main()


