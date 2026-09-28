"""Quant correctness regressions: synthetic accounting cases, not edge evidence."""
import datetime as dt

import pytest

from app.providers.base import SwapEvent
from app.wallet_intel import _ledger_stats


def test_feasibility_slippage_upper_bounds_simulated_buy_fill():
    from app.copy_feasibility import CopyFeasibility, SizingInput
    from app.execution import PaperExecution
    inp = SizingInput(equity=10000, our_size=1000, whale_trade_size=10000,
                      pool_depth=100000, volatility_bps=300)
    gate = CopyFeasibility().evaluate(inp)
    _, actual_bps = PaperExecution().fill_price("buy", 1, inp.our_size, inp.pool_depth)
    assert gate.slippage_bps >= actual_bps
    assert not gate.feasible


def test_ineligible_tournament_cannot_name_a_winner():
    from app.learning import Tournament
    result = Tournament().run_tournament([
        {"strategy_id": 0, "name": "tiny", "pnls": [1, 2, 3, 4, 5]},
    ])
    assert result["winner"] is None
    assert result["ranking"][0]["stat"]["ineligible"]


def swap(side, qty, price, minute, token="QUANT", tx=None):
    return SwapEvent(chain="solana", tx_hash=tx or f"quant-{token}-{minute}", wallet="quant-wallet",
                     pool_address="quant-pool", token_address=token, symbol="Q", side=side,
                     base_amount=qty, quote_amount=qty * price, value_usd=qty * price,
                     price_usd=price, block_time=dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=100-minute))


def test_realized_matches_quantity_weighted_inventory_not_current_mark():
    events = [swap("buy", 10, 10, 1), swap("buy", 30, 20, 2), swap("sell", 20, 30, 3)]
    a = _ledger_stats(events, {"QUANT": 100})
    b = _ledger_stats(events, {"QUANT": 1})
    assert a["realized_pnl_usd"] == pytest.approx(250)
    assert a["realized_pnl_pct"] == pytest.approx(250 / 350 * 100)
    assert b["realized_pnl_pct"] == a["realized_pnl_pct"]
    assert a["unrealized_pnl_usd"] == pytest.approx(1650)


def test_unmatched_early_sell_is_not_matched_to_later_buy():
    a = _ledger_stats([swap("buy", 1, 10, 2), swap("sell", 1, 100, 1)], {"QUANT": 500})
    assert a["realized_pnl_pct"] == 0
    assert a["resolved_trades"] == 0
    assert a["unmatched_sells"] == 1


def test_buy_only_history_has_no_realized_wins_or_fabricated_drawdown():
    a = _ledger_stats([swap("buy", 1, 10, 1)], {"QUANT": 100})
    assert a["realized_pnl_pct"] == 0
    assert a["win_rate"] == 0
    assert a["max_drawdown_pct"] is None
