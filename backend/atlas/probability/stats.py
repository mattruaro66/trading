"""Estatísticas auditáveis de resultados de operações hipotéticas.

Nenhum percentual é produzido sem amostra mínima: abaixo de ``min_sample`` o
resultado traz ``sufficient=False`` e a mensagem padrão de evidência insuficiente.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import stats as sps

INSUFFICIENT_MSG = "Evidência estatística insuficiente para estimar uma probabilidade confiável."
DEFAULT_MIN_SAMPLE = 30


def wilson_interval(successes: int, n: int, confidence: float = 0.95) -> tuple[float, float] | None:
    if n <= 0:
        return None
    z = float(sps.norm.ppf(1 - (1 - confidence) / 2))
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def bootstrap_mean_ci(values: list[float], confidence: float = 0.95, reps: int = 5000, seed: int = 42) -> tuple[float, float] | None:
    if len(values) < 2:
        return None
    rng = np.random.default_rng(seed)
    arr = np.asarray(values, dtype=float)
    idx = rng.integers(0, len(arr), size=(reps, len(arr)))
    means = arr[idx].mean(axis=1)
    lo, hi = np.quantile(means, [(1 - confidence) / 2, 1 - (1 - confidence) / 2])
    return float(lo), float(hi)


def max_drawdown(pnl: list[float]) -> tuple[float, int, int]:
    """Retorna (drawdown máximo em R$ — valor positivo, índice do pico, índice do vale) sobre o resultado acumulado."""
    peak, peak_i, mdd, mdd_peak, mdd_trough = 0.0, -1, 0.0, -1, -1
    cum = 0.0
    for i, x in enumerate(pnl):
        cum += x
        if cum > peak:
            peak, peak_i = cum, i
        dd = peak - cum
        if dd > mdd:
            mdd, mdd_peak, mdd_trough = dd, peak_i, i
    return round(mdd, 2), mdd_peak, mdd_trough


def max_consecutive(flags: list[bool]) -> int:
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


@dataclass
class TradeStats:
    n: int
    wins: int
    losses: int
    breakeven: int
    win_rate: float | None
    win_rate_ci95: tuple[float, float] | None
    avg_win_brl: float | None
    avg_loss_brl: float | None
    payoff: float | None
    expectancy_brl: float | None
    expectancy_brl_ci95: tuple[float, float] | None
    expectancy_r: float | None
    expectancy_r_ci95: tuple[float, float] | None
    profit_factor: float | None
    gross_brl: float
    costs_brl: float
    net_brl: float
    max_drawdown_brl: float
    max_consecutive_losses: int
    t_stat: float | None
    p_value_mean_gt_0: float | None
    sufficient: bool
    min_sample: int
    message: str | None

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def compute_stats(trades: list[dict], min_sample: int = DEFAULT_MIN_SAMPLE, seed: int = 42) -> TradeStats:
    net = [float(t["net_brl"]) for t in trades]
    gross = [float(t["gross_brl"]) for t in trades]
    costs = [float(t["costs_brl"]) for t in trades]
    rs = [float(t["net_r"]) for t in trades if t.get("net_r") is not None]
    n = len(net)
    wins = [x for x in net if x > 0]
    losses = [x for x in net if x < 0]
    be = n - len(wins) - len(losses)
    sufficient = n >= min_sample
    wr = len(wins) / n if n else None
    avg_w = float(np.mean(wins)) if wins else None
    avg_l = float(np.mean(losses)) if losses else None
    payoff = (avg_w / abs(avg_l)) if (avg_w is not None and avg_l) else None
    pf = (sum(wins) / abs(sum(losses))) if losses else (None if not wins else math.inf)
    exp = float(np.mean(net)) if n else None
    exp_r = float(np.mean(rs)) if rs else None
    t_stat = p_val = None
    if len(rs) >= 3 and np.std(rs, ddof=1) > 0:
        res = sps.ttest_1samp(rs, 0.0, alternative="greater")
        t_stat, p_val = float(res.statistic), float(res.pvalue)
    mdd, _, _ = max_drawdown(net)
    return TradeStats(
        n=n, wins=len(wins), losses=len(losses), breakeven=be, win_rate=wr,
        win_rate_ci95=wilson_interval(len(wins), n) if n else None,
        avg_win_brl=avg_w, avg_loss_brl=avg_l, payoff=payoff, expectancy_brl=exp,
        expectancy_brl_ci95=bootstrap_mean_ci(net, seed=seed), expectancy_r=exp_r,
        expectancy_r_ci95=bootstrap_mean_ci(rs, seed=seed), profit_factor=pf,
        gross_brl=round(sum(gross), 2), costs_brl=round(sum(costs), 2), net_brl=round(sum(net), 2),
        max_drawdown_brl=mdd, max_consecutive_losses=max_consecutive([x < 0 for x in net]),
        t_stat=t_stat, p_value_mean_gt_0=p_val, sufficient=sufficient, min_sample=min_sample,
        message=None if sufficient else f"{INSUFFICIENT_MSG} (n={n} < {min_sample})",
    )


def group_stats(trades: list[dict], key: str, min_sample: int = DEFAULT_MIN_SAMPLE) -> dict[str, dict]:
    groups: dict[str, list[dict]] = {}
    for t in trades:
        groups.setdefault(str(t.get(key)), []).append(t)
    out = {}
    for k, g in sorted(groups.items()):
        s = compute_stats(g, min_sample)
        out[k] = {"n": s.n, "win_rate": s.win_rate, "win_rate_ci95": s.win_rate_ci95, "expectancy_brl": s.expectancy_brl,
                  "expectancy_r": s.expectancy_r, "net_brl": s.net_brl, "profit_factor": s.profit_factor,
                  "sufficient": s.sufficient}
    return out


def equity_curve(trades: list[dict]) -> list[dict]:
    cum, out = 0.0, []
    for t in trades:
        cum += float(t["net_brl"])
        out.append({"ts": t["exit_ts"], "equity_brl": round(cum, 2), "trade_id": t["trade_id"]})
    return out


def distribution(values: list[float], bins: int = 20) -> dict:
    if not values:
        return {"bins": [], "counts": []}
    counts, edges = np.histogram(values, bins=min(bins, max(1, len(values))))
    return {"edges": [float(e) for e in edges], "counts": [int(c) for c in counts]}


def brier_score(probs: list[float], outcomes: list[int]) -> float | None:
    if not probs:
        return None
    p, y = np.asarray(probs, float), np.asarray(outcomes, float)
    return float(np.mean((p - y) ** 2))
