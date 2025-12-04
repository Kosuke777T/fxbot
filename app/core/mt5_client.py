import time
import os  # ★ 追加

import MetaTrader5 as MT5
import pandas as pd
from loguru import logger
from typing import Optional, Dict, Any
from typing import NamedTuple


POSITION_COLUMNS = [
    "ticket",
    "time",
    "time_msc",
    "time_update",
    "time_update_msc",
    "symbol",
    "magic",
    "volume",
    "price_open",
    "sl",
    "tp",
    "price_current",
    "swap",
    "profit",
    "comment",
]

class TickSpec(NamedTuple):
    tick_size: float   # 1ティックの価格幅（例: 0.01 JPY）
    tick_value: float  # 1ティック動いたときの損益（1ロットあたりの通貨）


class MT5Client:
    """MT5 発注・接続ラッパー（最小構成）"""

    def __init__(self, login: int, password: str, server: str, timeout: float = 5.0):
        self.login = login
        self.password = password
        self.server = server
        self.timeout = timeout
        self.connected = False
        self.logger = logger

    # ------------------------
    # 接続系
    # ------------------------
    def initialize(self) -> bool:
        """MT5ターミナルの初期化（ログインは login_account()）"""
        logger.info("MT5 initialize() called...")

        if not MT5.initialize():
            err = MT5.last_error()
            logger.error(f"MT5 initialize() failed: {err}")
            self.connected = False
            return False

        logger.info("MT5 initialize() succeeded")
        self.connected = True
        return True

    def login_account(self) -> bool:
        """設定されたログイン情報で MT5.login() を実行"""
        logger.info(
            f"MT5 login() called with login={self.login}, server={self.server}"
        )

        ok = MT5.login(
            self.login,
            password=self.password,
            server=self.server,
        )
        if not ok:
            err = MT5.last_error()
            logger.error(f"MT5 login() failed: {err}")
            return False

        logger.info("MT5 login() succeeded")
        return True

    def shutdown(self):
        """MT5 をシャットダウン"""
        logger.info("MT5 shutdown()")
        MT5.shutdown()
        self.connected = False

    # ------------------------
    # 発注
    # ------------------------
    def order_send(
        self,
        symbol: str,
        order_type: str,
        lot: float,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        retries: int = 3,
    ) -> Optional[int]:
        """
        成行発注（BUY / SELL）

        Parameters
        ----------
        symbol : str
        order_type : "BUY" or "SELL"
        lot : float
        sl, tp : Optional[float]
        retries : int

        Returns
        -------
        Optional[int]
            成功: チケット番号（int）
            失敗: None
        """

        if order_type not in ("BUY", "SELL"):
            raise ValueError(f"order_type must be BUY/SELL: got {order_type}")

        # --- 1) シンボル情報をチェック ---
        info = MT5.symbol_info(symbol)
        if info is None:
            logger.error(f"[order_send] symbol_info({symbol}) が None。シンボルが存在しない可能性")
            return None

        if not info.visible:
            logger.info(f"[order_send] {symbol} が非表示なので symbol_select() します")
            if not MT5.symbol_select(symbol, True):
                logger.error(f"[order_send] symbol_select({symbol}, True) に失敗")
                return None

        # --- 2) 最新ティック ---
        tick = MT5.symbol_info_tick(symbol)
        if tick is None:
            logger.error(f"[order_send] symbol_info_tick({symbol}) が None。ティックが取得できない")
            return None

        # --- 3) 注文種別と価格 ---
        if order_type == "BUY":
            mt_type = MT5.ORDER_TYPE_BUY
            price = tick.ask
        else:
            mt_type = MT5.ORDER_TYPE_SELL
            price = tick.bid

        # --- 4) 注文リクエスト ---
        request: Dict[str, Any] = {
            "action": MT5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(lot),
            "type": mt_type,
            "price": float(price),
            "sl": float(sl) if sl is not None else 0.0,
            "tp": float(tp) if tp is not None else 0.0,
            "magic": 123456,
            "comment": "fxbot_test_order",
            "type_time": MT5.ORDER_TIME_GTC,
            "type_filling": MT5.ORDER_FILLING_FOK,
        }

        last_error: Optional[tuple[int, str]] = None

        # --- 5) リトライ付き order_send ---
        for attempt in range(1, retries + 1):
            logger.info(
                f"[order_send] Try {attempt}/{retries}: {order_type} {lot} lot @ {price} {symbol}"
            )

            result = MT5.order_send(request)

            if result is None:
                last_error = MT5.last_error()
                logger.error(f"[order_send] result is None, last_error={last_error}")

            else:
                logger.info(
                    "[order_send] retcode=%s, order=%s, deal=%s, comment=%s",
                    getattr(result, "retcode", None),
                    getattr(result, "order", None),
                    getattr(result, "deal", None),
                    getattr(result, "comment", None),
                )

                # 成行なので DONE = 成功
                if result.retcode == MT5.TRADE_RETCODE_DONE:
                    ticket = int(result.order or result.deal or 0)
                    if ticket > 0:
                        logger.info(f"[order_send] 成功: ticket={ticket}")
                        return ticket
                    else:
                        logger.warning(f"[order_send] DONE だが ticket が取得できない: {result}")

                else:
                    logger.warning(
                        f"[order_send] 失敗 retcode={result.retcode}。再試行する場合があります"
                    )

            if attempt < retries:
                time.sleep(1.0)

        logger.error(f"[order_send] 全 {retries} 回リトライしても失敗。last_error={last_error}")
        return None

    # ------------------------
    # 決済（クローズ）
    # ------------------------
    def close_position(self, ticket: int, symbol: str, retries: int = 3) -> bool:
        """指定チケットの成行クローズ"""

        pos = MT5.positions_get(ticket=ticket)
        if not pos:
            logger.error(f"ticket={ticket} のポジションが存在しません")
            return False

        position = pos[0]
        lot = position.volume

        # position.type: 0=BUY, 1=SELL
        order_type = MT5.ORDER_TYPE_SELL if position.type == 0 else MT5.ORDER_TYPE_BUY

        # クローズ価格
        t = MT5.symbol_info_tick(symbol)
        if t is None:
            logger.error(f"[close_position] symbol_info_tick({symbol}) が None")
            return False

        price = t.bid if order_type == MT5.ORDER_TYPE_SELL else t.ask

        request = {
            "action": MT5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": order_type,
            "position": ticket,
            "price": price,
            "magic": 123456,
            "comment": "fxbot_test_close",
            "type_time": MT5.ORDER_TIME_GTC,
            "type_filling": MT5.ORDER_FILLING_FOK,
        }

        for attempt in range(1, retries + 1):
            logger.info(f"[close_position] Try {attempt}: ticket={ticket}")
            result = MT5.order_send(request)

            if result and result.retcode == MT5.TRADE_RETCODE_DONE:
                logger.info(f"クローズ成功: ticket={ticket}")
                return True

            logger.error(
                f"retcode={result.retcode if result else None}, err={MT5.last_error()}"
            )
            time.sleep(1.0)

        logger.error("[close_position] 全リトライ失敗")
        return False

    # ------------------------
    # ポジション一覧
    # ------------------------
    def get_positions(self):
        try:
            pos = MT5.positions_get()
            if pos is None:
                self.logger.warning("positions_get() returned None")
                return []
            return list(pos)
        except Exception as exc:
            self.logger.exception(f"positions_get() failed: {exc}")
            return []

    def get_positions_by_symbol(self, symbol: str):
        rows = self.get_positions()
        out = [p for p in rows if getattr(p, "symbol", None) == symbol]
        self.logger.info(f"get_positions_by_symbol: {symbol} count={len(out)}")
        return out

    def get_positions_df(self, symbol: Optional[str] = None):
        rows = self.get_positions()
        if symbol:
            rows = [p for p in rows if getattr(p, "symbol", None) == symbol]

        if not rows:
            return pd.DataFrame(columns=POSITION_COLUMNS)

        data = []
        for p in rows:
            data.append(
                {
                    "ticket": p.ticket,
                    "time": p.time,
                    "time_msc": p.time_msc,
                    "time_update": p.time_update,
                    "time_update_msc": p.time_update_msc,
                    "symbol": p.symbol,
                    "magic": p.magic,
                    "volume": p.volume,
                    "price_open": p.price_open,
                    "sl": p.sl,
                    "tp": p.tp,
                    "price_current": p.price_current,
                    "swap": p.swap,
                    "profit": p.profit,
                    "comment": p.comment,
                }
            )

        return pd.DataFrame(data, columns=POSITION_COLUMNS)

    def get_equity(self) -> float:
        """現在口座の有効証拠金（equity）を返す。"""
        info = MT5.account_info()
        if info is None:
            raise RuntimeError("account_info() が None を返しました（MT5 接続を確認してください）")

        return float(info.equity)

    def get_tick_spec(self, symbol: str) -> TickSpec:
        """
        指定シンボルの tick_size / tick_value を返す。
        - tick_size: 価格が 1 tick 動く幅
        - tick_value: その 1 tick で 1 ロットあたりの損益
        """
        info = MT5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(f"symbol_info({symbol!r}) が None を返しました（シンボル名を確認してください）")

        # broker によっては trade_tick_size / trade_tick_value を使う場合もあります。
        # ここでは point / trade_tick_value を使う想定です。
        tick_size = float(getattr(info, "point", 0.0))
        tick_value = float(getattr(info, "trade_tick_value", 0.0))

        if tick_size <= 0:
            raise RuntimeError(f"{symbol!r} の tick_size が 0 以下です: {tick_size}")
        if tick_value <= 0:
            raise RuntimeError(f"{symbol!r} の tick_value が 0 以下です: {tick_value}")

        return TickSpec(tick_size=tick_size, tick_value=tick_value)


# ================================
# モジュールレベルのシングルトン & ラッパーAPI
# ================================

_client: Optional[MT5Client] = None


def _get_env(name: str) -> str:
    """必須の環境変数を取得（なければ RuntimeError）"""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"環境変数 {name} が設定されていません。"
            "設定タブから MT5 口座プロファイルを選び、"
            "apply_env=True で MT5_LOGIN / MT5_PASSWORD / MT5_SERVER を適用してください。"
        )
    return value


def _get_client() -> MT5Client:
    """
    環境変数 MT5_LOGIN / PASSWORD / SERVER から MT5Client の
    シングルトンインスタンスを生成して返す。
    """
    global _client
    if _client is not None:
        return _client

    login = int(_get_env("MT5_LOGIN"))
    password = _get_env("MT5_PASSWORD")
    server = _get_env("MT5_SERVER")

    logger.info(
        f"[mt5_client] create MT5Client(login={login}, server={server}) "
        "(password はログに出しません)"
    )
    _client = MT5Client(login=login, password=password, server=server)
    return _client


def initialize() -> bool:
    """
    scripts/selftest_mt5.py などから呼ばれる想定のラッパー。
    MT5Client.initialize() を委譲する。
    """
    client = _get_client()
    return client.initialize()


def login() -> bool:
    """必要なら MT5Client.login_account() を呼ぶためのラッパー。"""
    client = _get_client()
    return client.login_account()


def shutdown() -> None:
    """
    MT5 のシャットダウンラッパー。

    _client がなくても MT5.shutdown() だけは呼んでおく。
    """
    global _client
    logger.info("[mt5_client] shutdown() called")

    if _client is not None:
        _client.shutdown()
        _client = None
    else:
        # 念のため直接 MT5.shutdown() も呼ぶ
        MT5.shutdown()


def get_account_info():
    """
    アカウント情報を取得するラッパー。
    とりあえず MetaTrader5 の account_info をそのまま返す。
    """
    info = MT5.account_info()
    if info is None:
        logger.error("[mt5_client] MT5.account_info() returned None")
        return None
    return info


def get_positions():
    """
    オープンポジション一覧（Rawのリスト）を返すラッパー。
    """
    client = _get_client()
    return client.get_positions()


def get_positions_df(symbol: Optional[str] = None):
    """
    オープンポジションを pandas.DataFrame で返すラッパー。
    """
    client = _get_client()
    return client.get_positions_df(symbol=symbol)


def get_equity() -> float:
    """
    有効証拠金（equity）を float で返すラッパー。
    """
    client = _get_client()
    return client.get_equity()


def get_tick_spec(symbol: str) -> TickSpec:
    """
    指定シンボルの TickSpec を返すラッパー。
    """
    client = _get_client()
    return client.get_tick_spec(symbol)
