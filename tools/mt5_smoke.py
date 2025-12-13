# tools/mt5_smoke.py
"""
MT5 接続・テスト発注のスモークテスト（CLI版）

Usage:
    python -m tools.mt5_smoke [--symbol SYMBOL] [--lot LOT] [--close-now 0|1] [--dry 0|1]

Examples:
    # デフォルト設定で実行（USDJPY-, 0.01 lot, 即クローズ）
    python -m tools.mt5_smoke

    # ドライラン（実際の発注なし）
    python -m tools.mt5_smoke --dry 1

    # 発注のみ（クローズしない）
    python -m tools.mt5_smoke --close-now 0

    # カスタムシンボルとロット
    python -m tools.mt5_smoke --symbol EURUSD- --lot 0.02
"""
from __future__ import annotations

import sys
import logging

# 標準loggingもstderrへ（app.services を import する前に設定）
logging.basicConfig(stream=sys.stderr)

# loguruもstderrへ（stdout禁止、app.services を import する前に設定）
try:
    from loguru import logger
    logger.remove()
    logger.add(sys.stderr, level="INFO")
except Exception:
    pass

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

# プロジェクトルートを sys.path に追加
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import mt5_client
from app.core.mt5_client import MT5Client
from app.services import mt5_account_store


def main() -> int:
    parser = argparse.ArgumentParser(
        description="MT5 接続・テスト発注のスモークテスト",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default="USDJPY-",
        help="テスト対象のシンボル（デフォルト: USDJPY-）",
    )
    parser.add_argument(
        "--lot",
        type=float,
        default=0.01,
        help="テスト発注のロット（デフォルト: 0.01）",
    )
    parser.add_argument(
        "--close-now",
        type=int,
        default=1,
        choices=[0, 1],
        help="発注後に即座にクローズするか（0=しない, 1=する、デフォルト: 1）",
    )
    parser.add_argument(
        "--dry",
        type=int,
        default=0,
        choices=[0, 1],
        help="ドライラン（実際の発注を行わない、デフォルト: 0）",
    )

    args = parser.parse_args()
    symbol = args.symbol
    lot = args.lot
    close_now = bool(args.close_now)
    dry = bool(args.dry)

    # 固定スキーマの結果
    result: Dict[str, Any] = {
        "ok": False,
        "step": "init",
        "error": None,
        "data": {},
        "meta": {
            "symbol": symbol,
            "mode": "dry" if dry else "live",
            "ts": datetime.now().isoformat(),
        },
    }
    exit_code = 1

    try:
        # 0) アクティブプロファイルから環境変数を適用
        result["step"] = "apply_env"
        active = mt5_account_store.get_active_profile_name()
        if not active:
            result["ok"] = False
            result["error"] = {
                "code": "NO_ACTIVE_PROFILE",
                "message": "アクティブなMT5口座プロファイルが設定されていません。",
                "detail": {},
            }
            return exit_code

        mt5_account_store.set_active_profile(active, apply_env=True)

        # 念のため毎回クリーンな状態から始める
        try:
            mt5_client.shutdown()
        except Exception:
            pass

        # 環境変数から MT5Client インスタンスを作成
        login_val = int(os.getenv("MT5_LOGIN", "0"))
        password_val = os.getenv("MT5_PASSWORD", "")
        server_val = os.getenv("MT5_SERVER", "")
        if not login_val or not password_val or not server_val:
            result["step"] = "create_client"
            result["error"] = {
                "code": "ENV_NOT_SET",
                "message": "環境変数 MT5_LOGIN/PASSWORD/SERVER が設定されていません。",
                "detail": {},
            }
            return exit_code

        client = MT5Client(login=login_val, password=password_val, server=server_val)

        # 1) initialize
        result["step"] = "init"
        ok = client.initialize()
        if not ok:
            result["ok"] = False
            result["error"] = {
                "code": "MT5_INIT_FAILED",
                "message": "MT5 の初期化に失敗しました。",
                "detail": {},
            }
            return exit_code

        # 2) login_account
        result["step"] = "login"
        ok = client.login_account()
        if not ok:
            result["ok"] = False
            result["error"] = {
                "code": "LOGIN_FAILED",
                "message": "MT5 へのログインに失敗しました。",
                "detail": {},
            }
            return exit_code

        # 3) account_info
        result["step"] = "account_info"
        info = mt5_client.get_account_info()
        if not info:
            result["ok"] = False
            result["error"] = {
                "code": "ACCOUNT_INFO_FAILED",
                "message": "get_account_info() が None を返しました。",
                "detail": {},
            }
            return exit_code

        # account_info を data に格納
        result["data"]["account"] = {
            "login": getattr(info, "login", None),
            "balance": getattr(info, "balance", None),
            "equity": getattr(info, "equity", None),
        }

        # 4) get_tick_spec
        result["step"] = "price"
        try:
            tick_spec = client.get_tick_spec(symbol)
            result["data"]["tick_spec"] = {
                "tick_size": tick_spec.tick_size,
                "tick_value": tick_spec.tick_value,
            }
        except Exception as e:
            result["ok"] = False
            msg = str(e)
            result["error"] = {
                "code": "PRICE_FAILED",
                "message": msg,
                "detail": {},
            }
            # 例外メッセージに "market closed" 等が含まれる場合は MARKET_CLOSED に上書き
            msg_lower = msg.lower()
            if any(keyword in msg_lower for keyword in ["market closed", "trade disabled", "trading disabled"]):
                result["error"]["code"] = "MARKET_CLOSED"
            return exit_code

        # 5) (dry=False の場合のみ) 成行発注
        if not dry:
            result["step"] = "order"
            try:
                # order_send を直接呼び出して retcode と last_error を取得できるようにする
                import MetaTrader5 as MT5  # type: ignore[import]

                # シンボル情報とティックを取得
                tick = MT5.symbol_info_tick(symbol)
                if tick is None:
                    result["ok"] = False
                    result["error"] = {
                        "code": "PRICE_FAILED",
                        "message": f"symbol_info_tick({symbol}) が None です。",
                        "detail": {},
                    }
                    return exit_code

                price = tick.ask  # BUY なので ask
                request = {
                    "action": MT5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "volume": float(lot),
                    "type": MT5.ORDER_TYPE_BUY,
                    "price": float(price),
                    "sl": 0.0,
                    "tp": 0.0,
                    "magic": 123456,
                    "comment": "fxbot_test_order",
                    "type_time": MT5.ORDER_TIME_GTC,
                    "type_filling": MT5.ORDER_FILLING_FOK,
                }

                trade_result = MT5.order_send(request)
                if trade_result is None:
                    # result が None の場合
                    last_error = MT5.last_error()
                    result["ok"] = False
                    result["error"] = {
                        "code": "ORDER_SEND_FAILED",
                        "message": "order_send() が None を返しました。",
                        "detail": {
                            "last_error": last_error,
                        },
                    }
                    return exit_code

                # retcode を確認
                retcode = getattr(trade_result, "retcode", None)
                if retcode != MT5.TRADE_RETCODE_DONE:
                    # 失敗した場合
                    last_error = MT5.last_error()
                    # retcode に応じて error.code を精密化
                    if retcode == 10018:
                        error_code = "MARKET_CLOSED"
                    elif retcode == 10017:
                        error_code = "TRADE_DISABLED"
                    elif retcode == 10014:
                        error_code = "INVALID_VOLUME"
                    else:
                        error_code = "ORDER_SEND_FAILED"

                    result["ok"] = False
                    result["error"] = {
                        "code": error_code,
                        "message": f"order_send() が失敗しました。retcode={retcode}",
                        "detail": {
                            "retcode": retcode,
                            "last_error": last_error,
                            "trade_result": {
                                "order": getattr(trade_result, "order", None),
                                "deal": getattr(trade_result, "deal", None),
                                "comment": getattr(trade_result, "comment", None),
                            },
                        },
                    }
                    return exit_code

                # 成功した場合
                ticket = int(trade_result.order or trade_result.deal or 0)
                if ticket <= 0:
                    result["ok"] = False
                    result["error"] = {
                        "code": "ORDER_SEND_FAILED",
                        "message": "order_send() が成功したが ticket が取得できませんでした。",
                        "detail": {
                            "retcode": retcode,
                            "trade_result": {
                                "order": getattr(trade_result, "order", None),
                                "deal": getattr(trade_result, "deal", None),
                            },
                        },
                    }
                    return exit_code

                result["data"]["order"] = {
                    "ticket": ticket,
                    "symbol": symbol,
                    "lot": lot,
                }

                # 6) (close_now=True の場合) 即クローズ
                if close_now:
                    # ポジション出現待ち（最大10秒）
                    result["step"] = "verify"
                    deadline = time.time() + 10.0
                    position_found = False
                    position_info = None
                    while time.time() < deadline:
                        pos = MT5.positions_get(ticket=ticket)
                        if pos:
                            position_found = True
                            position_info = pos[0]
                            break
                        time.sleep(0.5)

                    if not position_found:
                        result["ok"] = False
                        result["error"] = {
                            "code": "POSITION_NOT_FOUND",
                            "message": "発注後、ポジションが見つかりませんでした。",
                            "detail": {},
                        }
                        return exit_code

                    # position 情報を data に保存
                    if position_info:
                        result["data"]["position"] = {
                            "ticket": getattr(position_info, "ticket", None),
                            "volume": getattr(position_info, "volume", None),
                            "price_open": getattr(position_info, "price_open", None),
                            "symbol": getattr(position_info, "symbol", None),
                        }

                    result["step"] = "close"
                    ok = client.close_position(ticket=ticket, symbol=symbol)
                    if not ok:
                        result["ok"] = False
                        result["error"] = {
                            "code": "CLOSE_FAILED",
                            "message": "close_position() が失敗しました。",
                            "detail": {},
                        }
                        return exit_code

                    # close 成功情報を data に保存
                    result["data"]["close"] = {
                        "ticket": ticket,
                        "success": True,
                    }
            except Exception as e:
                result["ok"] = False
                msg = str(e)
                # 現在の step に応じて error.code を決定
                if result["step"] == "order":
                    code = "ORDER_SEND_FAILED"
                elif result["step"] == "verify":
                    code = "POSITION_NOT_FOUND"
                elif result["step"] == "close":
                    code = "CLOSE_FAILED"
                else:
                    code = "UNEXPECTED"

                result["error"] = {
                    "code": code,
                    "message": msg,
                    "detail": {},
                }
                # 例外メッセージに "market closed" 等が含まれる場合は MARKET_CLOSED に上書き
                msg_lower = msg.lower()
                if any(keyword in msg_lower for keyword in ["market closed", "trade disabled", "trading disabled"]):
                    result["error"]["code"] = "MARKET_CLOSED"
                return exit_code

        # 成功
        result["ok"] = True
        result["step"] = "done"
        exit_code = 0

    except Exception as e:
        # 予期しない例外
        result["ok"] = False
        msg = str(e)
        result["error"] = {
            "code": "UNEXPECTED",
            "message": msg,
            "detail": {},
        }
        exit_code = 1
    finally:
        # 必ず最後に1行で結果dictをJSON出力（stdout への出力はこの1回だけ）
        print(json.dumps(result, ensure_ascii=True, separators=(",", ":"), default=str))
        # 念のため shutdown
        try:
            mt5_client.shutdown()
        except Exception:
            pass

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

