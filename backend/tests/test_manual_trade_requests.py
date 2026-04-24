from kelly_watcher.main import ManualTradeRequest, _parse_manual_trade_request_payload


def test_parse_manual_buy_request_normalizes_fields():
    request = _parse_manual_trade_request_payload(
        {
            "action": "buy",
            "market_id": "0xmarket",
            "token_id": "123",
            "side": "YES",
            "question": "Will this parse?",
            "trader_address": "0xABC",
            "amount_usd": 12.5,
            "request_id": "req-1",
            "requested_at": 123456,
            "source": "dashboard",
        }
    )

    assert request == ManualTradeRequest(
        action="buy_more",
        market_id="0xmarket",
        token_id="123",
        side="yes",
        question="Will this parse?",
        trader_address="0xabc",
        amount_usd=12.5,
        request_id="req-1",
        requested_at=123456,
        source="dashboard",
    )


def test_parse_manual_cash_out_request_allows_empty_amount():
    request = _parse_manual_trade_request_payload(
        {
            "action": "sell_all",
            "market_id": "0xmarket",
            "token_id": "123",
            "side": "no",
            "request_id": "req-2",
            "requested_at": 123456,
        }
    )

    assert request.action == "cash_out"
    assert request.amount_usd is None


def test_parse_manual_buy_requires_positive_amount():
    try:
        _parse_manual_trade_request_payload(
            {
                "action": "buy_more",
                "market_id": "0xmarket",
                "token_id": "123",
                "side": "yes",
                "amount_usd": 0,
            }
        )
    except ValueError as exc:
        assert "positive amount_usd" in str(exc)
    else:
        raise AssertionError("expected invalid manual buy request to raise")
