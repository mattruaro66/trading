import math

import pytest

from atlas.backtest.costs import CostModel
from atlas.backtest.engine import run_backtest, simulate, walk_forward
from atlas.pipeline import AnalysisPipeline
from atlas.probability.stats import (
    INSUFFICIENT_MSG, bootstrap_mean_ci, compute_stats, max_consecutive, max_drawdown, wilson_interval,
)
from atlas.strategy.second_entry import SecondEntryParams


def outputs_signature(bars):
    pipe = AnalysisPipeline("WIN", SecondEntryParams())
    sig = []
    for b in bars:
        o = pipe.on_bar_closed(b)
        sig.append((o.snapshot.regime.regime, o.snapshot.bull_legs.event, o.snapshot.bear_legs.event,
                    tuple((s.side, s.entry_price, s.stop_price) for s in o.signals)))
    return sig


def test_truncation_invariance(synthetic_win):
    """O resultado no candle k não depende de candles posteriores (sem look-ahead)."""
    full = outputs_signature(synthetic_win)
    for k in (100, 777, 1500):
        assert outputs_signature(synthetic_win[:k]) == full[:k]


def test_future_mutation_does_not_change_past(synthetic_win):
    k = 900
    mutated = synthetic_win[:k] + [b.with_(open=b.open + 500, high=b.high + 600, low=b.low + 500, close=b.close + 500)
                                   for b in synthetic_win[k:]]
    assert outputs_signature(mutated)[:k] == outputs_signature(synthetic_win)[:k]


def test_pipeline_rejects_unordered(synthetic_win):
    pipe = AnalysisPipeline("WIN")
    pipe.on_bar_closed(synthetic_win[10])
    with pytest.raises(ValueError):
        pipe.on_bar_closed(synthetic_win[5])
    with pytest.raises(ValueError):
        AnalysisPipeline("WDO").on_bar_closed(synthetic_win[0])


def test_backtest_reproducible(synthetic_win):
    p = SecondEntryParams.for_profile("scalp")
    r1 = run_backtest(synthetic_win, "WIN", p)
    r2 = run_backtest(list(reversed(synthetic_win)), "WIN", p)  # ordem de entrada irrelevante
    assert r1["run_id"] == r2["run_id"]
    assert r1["stats"] == r2["stats"]
    assert r1["trades"] == r2["trades"]
    assert r1["meta"]["synthetic"] is True and "SINTÉTICOS" in r1["warnings"][0]


def test_backtest_accounting_consistency(synthetic_win):
    costs = CostModel(brokerage_per_contract_side=0.5, exchange_fees_per_contract_side=0.3, contracts=2)
    r = run_backtest(synthetic_win, "WIN", SecondEntryParams.for_profile("scalp"), costs)
    trades = r["trades"]
    assert trades
    s = r["stats"]["all"]
    assert s["n"] == len(trades)
    assert s["net_brl"] == pytest.approx(sum(t["net_brl"] for t in trades))
    assert s["gross_brl"] - s["costs_brl"] == pytest.approx(s["net_brl"], abs=0.01)
    for t in trades:
        assert t["costs_brl"] == pytest.approx(3.2)  # 0,8 × 2 lados × 2 contratos
        assert t["gross_brl"] == pytest.approx(t["points"] * 0.20 * 2)
        assert t["points"] % 5 == 0  # resultados múltiplos do tick do WIN
    assert r["stats"]["train"]["n"] + r["stats"]["test"]["n"] == s["n"]
    assert r["equity_curve"][-1]["equity_brl"] == pytest.approx(s["net_brl"])


def test_backtest_wdo(synthetic_wdo):
    r = run_backtest(synthetic_wdo, "WDO", SecondEntryParams.for_profile("scalp"))
    for t in r["trades"]:
        assert (t["points"] / 0.5) == pytest.approx(round(t["points"] / 0.5))
        assert t["gross_brl"] == pytest.approx(t["points"] * 10.0)


def test_stats_math():
    trades = [{"net_brl": x, "gross_brl": x, "costs_brl": 0.0, "net_r": x / 10} for x in [10, -5, 20, -5, -5, 15]]
    s = compute_stats(trades, min_sample=3)
    assert s.n == 6 and s.wins == 3 and s.losses == 3
    assert s.win_rate == 0.5
    assert s.avg_win_brl == 15 and s.avg_loss_brl == -5 and s.payoff == 3
    assert s.profit_factor == pytest.approx(45 / 15)
    assert s.expectancy_brl == pytest.approx(30 / 6)
    assert s.max_consecutive_losses == 2
    assert s.max_drawdown_brl == 10  # 30 → 20
    assert s.sufficient


def test_insufficient_sample_message():
    s = compute_stats([{"net_brl": 1, "gross_brl": 1, "costs_brl": 0, "net_r": 0.1}], min_sample=30)
    assert not s.sufficient and INSUFFICIENT_MSG in s.message


def test_wilson_and_bootstrap():
    lo, hi = wilson_interval(50, 100)
    assert lo == pytest.approx(0.4038, abs=1e-3) and hi == pytest.approx(0.5962, abs=1e-3)
    assert wilson_interval(0, 0) is None
    ci1 = bootstrap_mean_ci([1, 2, 3, 4, 5], seed=1)
    assert ci1 == bootstrap_mean_ci([1, 2, 3, 4, 5], seed=1)  # semeado → reproduzível
    assert ci1[0] <= 3 <= ci1[1]
    assert max_drawdown([5, -10, 3, -4])[0] == 11
    assert max_consecutive([True, True, False, True]) == 2


def test_probability_event_defined(synthetic_win):
    r = run_backtest(synthetic_win, "WIN", SecondEntryParams.for_profile("scalp"), min_sample=10_000)
    p = r["probability"]
    assert "antes do stop de 1R" in p["event"] and "5 minutos" in p["event"]
    assert p["p_hat"] is None and INSUFFICIENT_MSG in p["message"]  # nunca inventa percentual


def test_walk_forward_runs(synthetic_win):
    wf = walk_forward(synthetic_win, "WIN", {"target_r": [1.0, 2.0]}, folds=3, min_train_trades=3, min_sample=5)
    assert wf["configurations_tested"] == 2
    assert len(wf["folds"]) == 3
    assert wf["oos_stats"]["n"] >= 0
    p = wf["oos_p_value_bonferroni"]
    assert p is None or 0 <= p <= 1


def test_simulate_counters(synthetic_win):
    trades, c = simulate(synthetic_win, "WIN", SecondEntryParams.for_profile("scalp"), CostModel())
    resolved = c["filled"] + c["order_expired"] + c["order_cancelled"] + c["signal_skipped"]
    assert c["signals"] - resolved in (0, 1)  # no máximo uma ordem pendente no último candle
    assert c["filled"] - len(trades) in (0, 1)  # no máximo uma posição aberta no fim dos dados
    assert not any(math.isnan(t["net_brl"]) for t in trades)
