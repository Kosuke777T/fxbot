from pathlib import Path
path=Path('app/core/mt5_client.py')
text=path.read_text('utf-8')
start=text.index('    # ------------------------\n    # ����\n    # ------------------------\n    def order_send')
end=text.index('    # ------------------------\n    # ����', start+1)
new= """    # ------------------------
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
        成行注文を送る簡易ラッパー。

        order_type : \"BUY\" or \"SELL\"
        lot        : ロット数
        sl, tp     : 価格（None または 0 なら付けない）
        戻り値     : 成功したら ticket (int)、失敗したら None
        """

        if order_type not in (\"BUY\", \"SELL\"):
            raise ValueError(f\"order_type must be BUY/SELL: got {order_type}\")

        # 1) シンボル情報を取得して、見えない場合は symbol_select する
        info = MT5.symbol_info(symbol)
        if info is None:
            logger.error(f\"[order_send] symbol_info({symbol}) が None。シンボルが存在しない可能性\")
            return None

        if not info.visible:
            logger.info(f\"[order_send] {symbol} が非表示なので symbol_select します\")
            if not MT5.symbol_select(symbol, True):
                logger.error(f\"[order_send] symbol_select({symbol}, True) に失敗\")
                return None

        # 2) 現在ティック（Bid/Ask）を取得
        tick = MT5.symbol_info_tick(symbol)
        if tick is None:
            logger.error(f\"[order_send] symbol_info_tick({symbol}) が None。ティックが取得できない\")
            return None

        # 3) 成行価格と注文タイプを決定
        if order_type == \"BUY\":
            mt_type = MT5.ORDER_TYPE_BUY
            price = float(tick.ask)
        else:
            mt_type = MT5.ORDER_TYPE_SELL
            price = float(tick.bid)

        # 4) 注文リクエストを組み立て
        request: Dict[str, Any] = {
            \"action\": MT5.TRADE_ACTION_DEAL,
            \"symbol\": symbol,
            \"volume\": float(lot),
            \"type\": mt_type,
            \"price\": price,
            \"sl\": float(sl) if sl else 0.0,
            \"tp\": float(tp) if tp else 0.0,
            \"magic\": 123456,
            \"comment\": \"fxbot_test_order\",
            \"type_time\": MT5.ORDER_TIME_GTC,
            \"type_filling\": MT5.ORDER_FILLING_FOK,
        }

        last_error: Optional[tuple[int, str]] = None

        # 5) リトライ付きで order_send
        for attempt in range(1, retries + 1):
            logger.info(
                \"[order_send] Try {}/{}: {} {} lot @ {} {}\",
                attempt,
                retries,
                order_type,
                lot,
                price,
                symbol,
            )
            result = MT5.order_send(request)

            if result is None:
                last_error = MT5.last_error()
                logger.error(f\"[order_send] result is None, last_error={last_error}\")
            else:
                logger.info(
                    \"[order_send] retcode={}, order={}, deal={}, comment={}\",
                    getattr(result, \"retcode\", None),
                    getattr(result, \"order\", None),
                    getattr(result, \"deal\", None),
                    getattr(result, \"comment\", None),
                )

                # 成行なので DONE 系を成功判定とする
                if result.retcode == MT5.TRADE_RETCODE_DONE:
                    ticket = int(result.order or result.deal or 0)
                    if ticket > 0:
                        logger.info(f\"[order_send] 成功: ticket={ticket}\")
                        return ticket
                    else:
                        logger.warning(
                            f\"[order_send] DONE だが ticket が取得できない: result={result}\"
                        )
                else:
                    logger.warning(
                        f\"[order_send] 失敗 retcode={result.retcode}。必要なら再試行…\"
                    )

            if attempt < retries:
                time.sleep(1.0)

        logger.error(f\"[order_send] 全 {retries} 回リトライしても失敗。last_error={last_error}\")
        return None
"""
text = text[:start] + new + text[end:]
path.write_text(text, 'utf-8')
