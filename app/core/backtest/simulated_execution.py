# app/core/backtest/simulated_execution.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import pandas as pd


@dataclass
class SimulatedTrade:
    """シミュレートされたトレード"""
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp
    exit_price: float
    side: str  # "BUY" or "SELL"
    lot: float
    pnl: float
    atr: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None


class SimulatedExecution:
    """
    バックテスト用のシミュレート実行エンジン

    - ポジションを開いて、終了条件に達したらクローズする
    - トレード履歴を記録する
    """

    def __init__(self, initial_capital: float = 100000.0, contract_size: int = 100000):
        """
        Parameters
        ----------
        initial_capital : float
            初期資本
        contract_size : int
            契約サイズ（JPYペアの場合は100000）
        """
        self.initial_capital = initial_capital
        self.contract_size = contract_size
        self.equity = initial_capital
        self.trades: List[SimulatedTrade] = []
        self._open_position: Optional[SimulatedTrade] = None

    def open_position(
        self,
        side: str,
        price: float,
        timestamp: pd.Timestamp,
        lot: float = 0.1,
        atr: Optional[float] = None,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
    ) -> None:
        """
        ポジションを開く

        Parameters
        ----------
        side : str
            "BUY" or "SELL"
        price : float
            エントリー価格
        timestamp : pd.Timestamp
            エントリー時刻
        lot : float
            ロットサイズ
        atr : float, optional
            ATR値
        sl : float, optional
            ストップロス価格
        tp : float, optional
            テイクプロフィット価格
        """
        # 既にポジションが開いている場合はクローズしてから開く
        if self._open_position is not None:
            self.close_position(price, timestamp)

        self._open_position = SimulatedTrade(
            entry_time=timestamp,
            entry_price=price,
            exit_time=timestamp,  # 仮値
            exit_price=price,  # 仮値
            side=side.upper(),
            lot=lot,
            pnl=0.0,
            atr=atr,
            sl=sl,
            tp=tp,
        )

    def close_position(self, price: float, timestamp: pd.Timestamp) -> Optional[SimulatedTrade]:
        """
        ポジションをクローズする

        Parameters
        ----------
        price : float
            クローズ価格
        timestamp : pd.Timestamp
            クローズ時刻

        Returns
        -------
        SimulatedTrade or None
            クローズされたトレード（ポジションが開いていなかった場合はNone）
        """
        if self._open_position is None:
            return None

        trade = self._open_position
        trade.exit_time = timestamp
        trade.exit_price = price

        # PnL計算（JPYペア想定）
        if trade.side == "BUY":
            price_diff = price - trade.entry_price
        else:  # SELL
            price_diff = trade.entry_price - price

        trade.pnl = price_diff * trade.lot * self.contract_size
        self.equity += trade.pnl

        self.trades.append(trade)
        self._open_position = None

        return trade

    def force_close_all(self, price: float, timestamp: pd.Timestamp) -> None:
        """
        最終バーで強制的にすべてのポジションをクローズする
        """
        if self._open_position is not None:
            self.close_position(price, timestamp)

    def get_trades_df(self) -> pd.DataFrame:
        """
        トレード履歴をDataFrame形式で返す

        Returns
        -------
        pd.DataFrame
            カラム: entry_time, entry_price, exit_time, exit_price, side, lot, pnl, atr, sl, tp
        """
        if not self.trades:
            return pd.DataFrame(columns=[
                "entry_time", "entry_price", "exit_time", "exit_price",
                "side", "lot", "pnl", "atr", "sl", "tp"
            ])

        rows = []
        for trade in self.trades:
            rows.append({
                "entry_time": trade.entry_time,
                "entry_price": trade.entry_price,
                "exit_time": trade.exit_time,
                "exit_price": trade.exit_price,
                "side": trade.side,
                "lot": trade.lot,
                "pnl": trade.pnl,
                "atr": trade.atr,
                "sl": trade.sl,
                "tp": trade.tp,
            })

        return pd.DataFrame(rows)

    def get_equity_curve(self, timestamps: pd.Series, prices: pd.Series) -> pd.Series:
        """
        エクイティ曲線を生成する

        Parameters
        ----------
        timestamps : pd.Series
            時系列のタイムスタンプ
        prices : pd.Series
            時系列の価格

        Returns
        -------
        pd.Series
            エクイティ曲線（インデックスはtimestamps）
        """
        equity_series = pd.Series(self.initial_capital, index=timestamps)
        cum_equity = self.initial_capital

        # トレードを時系列順にソート
        sorted_trades = sorted(self.trades, key=lambda t: t.exit_time)

        trade_idx = 0
        for i, ts in enumerate(timestamps):
            # この時点までに決済されたトレードの損益を加算
            while trade_idx < len(sorted_trades) and sorted_trades[trade_idx].exit_time <= ts:
                cum_equity += sorted_trades[trade_idx].pnl
                trade_idx += 1

            equity_series.iloc[i] = cum_equity

        return equity_series

