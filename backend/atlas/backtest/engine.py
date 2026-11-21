"""Backtesting Engine para WIN/WDO (5 min).

Executa o MESMO ``AnalysisPipeline`` usado no replay e no live, candle a candle.
Resultados são simulações: não representam garantia de execução ou rentabilidade.
"""
from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import dataclass
from datetime import datetime

from atlas.backtest.costs import CostModel
from atlas.contracts.calendar import TradingCalendar, load_calendar
from atlas.contracts.specs import get_spec
from atlas.core.types import Bar, Symbol
from atlas.pipeline import AnalysisPipeline
from atlas.price_action.engine import ENGINE_VERSION, PriceActionParams
from atlas.probability.events import describe_event, estimate_target_first
from atlas.probability.stats import DEFAULT_MIN_SAMPLE, compute_stats, distribution, equity_curve, group_stats
from atlas.strategy import h2l2_momentum as _h2l2
from atlas.strategy.second_entry import STRATEGY_ID, STRATEGY_VERSION, SecondEntryParams


def _strategy_ids(params) -> tuple[str, str]:
    from atlas.strategy import brooks_method as _bm
    if isinstance(params, _bm.BrooksMethodParams):
        return _bm.STRATEGY_ID, _bm.STRATEGY_VERSION
    if isinstance(params, _h2l2.H2L2MomentumParams):
        return _h2l2.STRATEGY_ID, _h2l2.STRATEGY_VERSION
    return STRATEGY_ID, STRATEGY_VERSION

DISCLAIMER = ("Resultados de simulação histórica com premissas documentadas (política intrabar conservadora, slippage "
              "fixo). Não constituem garantia de execução, de rentabilidade nem recomendação de investimento.")


def _ts(trade: dict) -> datetime:
    return datetime.fromisoformat(trade["signal_ts"])


def data_fingerprint(bars: list[Bar]) -> str:
    h = hashlib.sha256()
    for b in bars:
        h.update(f"{b.contract}|{b.ts_open.isoformat()}|{b.open}|{b.high}|{b.low}|{b.close}|{b.volume}\n".encode())
    return h.hexdigest()


def simulate(bars: list[Bar], symbol: Symbol | str, params: SecondEntryParams, costs: CostModel,
             pa_params: PriceActionParams | None = None, calendar: TradingCalendar | None = None) -> tuple[list[dict], dict]:
    """Roda o pipeline sobre os candles e devolve (trades fechados, contadores de eventos)."""
    pipe = AnalysisPipeline(symbol, params, pa_params, costs, calendar)
    counters = {"signals": 0, "order_expired": 0, "order_cancelled": 0, "signal_skipped": 0, "filled": 0,
                "setups_h2_candidates": 0}
    for b in bars:
        out = pipe.on_bar_closed(b)
        counters["setups_h2_candidates"] += sum(1 for e in out.evaluations if e.stage == "candidate")
        counters["signals"] += len(out.signals)
        for ev in out.exec_events:
            if ev.kind in counters:
                counters[ev.kind] += 1
    trades = [t.as_dict() for t in pipe.sim.closed]
    return trades, counters


@dataclass
class SplitSpec:
    train_fraction: float = 0.7

    def cutoff(self, bars: list[Bar]) -> datetime | None:
        if len(bars) < 2:
            return None
        return bars[int(len(bars) * self.train_fraction)].ts_open


def run_backtest(bars: list[Bar], symbol: Symbol | str, params: SecondEntryParams | None = None,
                 costs: CostModel | None = None, *, train_fraction: float = 0.7,
                 min_sample: int = DEFAULT_MIN_SAMPLE, pa_params: PriceActionParams | None = None,
                 calendar: TradingCalendar | None = None, include_trades: bool = True) -> dict:
    sym = Symbol(symbol)
    params = params or SecondEntryParams.for_profile("daytrade")
    costs = costs or CostModel()
    cal = calendar or load_calendar()
    if not bars:
        raise ValueError("Nenhum candle para o backtest")
    bars = sorted(bars, key=lambda b: b.ts_open)
    trades, counters = simulate(bars, sym, params, costs, pa_params, cal)
    cutoff = SplitSpec(train_fraction).cutoff(bars)
    train = [t for t in trades if cutoff is None or _ts(t) < cutoff]
    test = [t for t in trades if cutoff is not None and _ts(t) >= cutoff]
    spec = get_spec(sym)
    synthetic = any(b.synthetic for b in bars)
    sources = sorted({b.source for b in bars})
    contracts = sorted({b.contract or "continuous" for b in bars})
    sid, sver = _strategy_ids(params)
    event = describe_event(sym.value, sid, params.target_r, params.time_exit_bars)
    fp = data_fingerprint(bars)
    config = {"strategy_id": sid, "strategy_version": sver, "engine_version": ENGINE_VERSION,
              "params": params.model_dump(mode="json"), "costs": costs.model_dump(), "train_fraction": train_fraction,
              "min_sample": min_sample, "data_fingerprint": fp}
    run_id = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:20]
    warnings = list(costs.warnings())
    if synthetic:
        warnings.insert(0, "DADOS SINTÉTICOS: resultados servem apenas para testar o software e não dizem nada sobre o mercado real.")
    if not spec.validated:
        warnings.append("Especificação do contrato ainda não validada contra a B3 (validated=false).")
    ambiguous = sum(1 for t in trades if t["ambiguous_bar"])
    if ambiguous:
        warnings.append(f"{ambiguous} operação(ões) com stop e alvo no mesmo candle: resolvidas como stop (política conservadora).")

    result = {
        "run_id": run_id,
        "config": config,
        "meta": {"symbol": sym.value, "contracts": contracts, "bars": len(bars),
                 "first_ts": bars[0].ts_open.isoformat(), "last_ts": bars[-1].ts_open.isoformat(),
                 "train_test_cutoff": cutoff.isoformat() if cutoff else None, "sources": sources,
                 "synthetic": synthetic, "tick_size": spec.tick_size, "point_value_brl": spec.point_value_brl,
                 "tick_value_brl": spec.tick_value_brl, "spec_version": spec.version, "spec_validated": spec.validated,
                 "counters": counters},
        "warnings": warnings,
        "disclaimer": DISCLAIMER,
        "stats": {"all": compute_stats(trades, min_sample).as_dict(),
                  "train": compute_stats(train, min_sample).as_dict(),
                  "test": compute_stats(test, min_sample).as_dict()},
        "breakdown": {"by_hour": group_stats(trades, "hour", min_sample), "by_regime": group_stats(trades, "regime", min_sample),
                      "by_side": group_stats(trades, "side", min_sample), "by_exit_reason": group_stats(trades, "exit_reason", min_sample),
                      "by_contract": group_stats(trades, "contract", min_sample)},
        "equity_curve": equity_curve(trades),
        "distribution_net_r": distribution([t["net_r"] for t in trades if t["net_r"] is not None]),
        "probability": estimate_target_first(train, test, event, min_sample).as_dict(),
    }
    if include_trades:
        result["trades"] = trades
    return result


def walk_forward(bars: list[Bar], symbol: Symbol | str, grid: dict[str, list], base: SecondEntryParams | None = None,
                 costs: CostModel | None = None, *, folds: int = 4, min_train_trades: int = 10,
                 min_sample: int = DEFAULT_MIN_SAMPLE, calendar: TradingCalendar | None = None) -> dict:
    """Walk-forward ancorado: em cada dobra escolhe, no treino, a configuração de maior expectativa (R líquido)
    e avalia-a na janela seguinte. Reporta o número de configurações testadas e p-valor com Bonferroni."""
    sym = Symbol(symbol)
    base = base or SecondEntryParams.for_profile("daytrade")
    costs = costs or CostModel()
    bars = sorted(bars, key=lambda b: b.ts_open)
    keys = sorted(grid)
    combos = [dict(zip(keys, vals)) for vals in itertools.product(*(grid[k] for k in keys))] or [{}]
    runs = []
    for combo in combos:
        p = base.model_copy(update=combo)
        trades, _ = simulate(bars, sym, p, costs, calendar=calendar)
        runs.append((combo, trades))
    n = len(bars)
    edges: list[datetime | None] = [bars[min(n - 1, int(n * i / (folds + 1)))].ts_open for i in range(folds + 2)]
    edges[-1] = None  # última janela de teste vai até o fim
    fold_reports, oos_trades = [], []
    for k in range(1, folds + 1):
        train_end, test_end = edges[k], edges[k + 1]
        best = None
        for combo, trades in runs:
            tr = [t for t in trades if _ts(t) < train_end]
            if len(tr) < min_train_trades:
                continue
            s = compute_stats(tr, min_sample)
            score = s.expectancy_r if s.expectancy_r is not None else float("-inf")
            if best is None or score > best[0]:
                best = (score, combo, trades, s)
        if best is None:
            fold_reports.append({"fold": k, "train_end": train_end.isoformat(), "selected": None,
                                 "note": f"nenhuma configuração com ≥{min_train_trades} operações no treino"})
            continue
        _, combo, trades, s_tr = best
        te = [t for t in trades if train_end <= _ts(t) and (test_end is None or _ts(t) < test_end)]
        oos_trades.extend(te)
        s_te = compute_stats(te, min_sample)
        fold_reports.append({"fold": k, "train_end": train_end.isoformat(), "test_end": test_end.isoformat() if test_end else None,
                             "selected": combo, "train_expectancy_r": s_tr.expectancy_r, "train_n": s_tr.n,
                             "test_expectancy_r": s_te.expectancy_r, "test_n": s_te.n, "test_net_brl": s_te.net_brl})
    oos = compute_stats(oos_trades, min_sample)
    p_adj = min(1.0, oos.p_value_mean_gt_0 * len(combos)) if oos.p_value_mean_gt_0 is not None else None
    return {"configurations_tested": len(combos), "grid": grid, "folds": fold_reports, "oos_stats": oos.as_dict(),
            "oos_p_value_bonferroni": p_adj,
            "note": "O p-valor ajustado (Bonferroni) é conservador; escolha de parâmetros no treino é refeita a cada dobra."}
