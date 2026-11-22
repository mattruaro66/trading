"""Gráficos reais (candles de 5 min) de um exemplo de operação perdedora por setup da estratégia C.

Uso: python scripts/plot_losing_trades.py RESULTADO_EURUSD.json [...] --out reports/losers
Cada RESULTADO_*.json: {"cut": ..., "trades": [...]} gerado pelo run_backtest (trades com lotes scalp/swing).
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from atlas.market_data.importers import import_bars  # noqa: E402
from atlas.strategy.brooks_method import SETUP_NAMES  # noqa: E402

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
OFFS = {"USDBRL": (2, 3)}
REASON = {"stop": "stop", "stop_gap": "stop (gap)", "breakeven": "zero a zero", "session_end": "fim da sessão",
          "target": "alvo", "data_gap_exit": "lacuna de dados", "target_gap": "alvo (gap)"}


def pick_examples(files):
    sigs = collections.defaultdict(list)
    for f in files:
        d = json.loads(Path(f).read_text())
        sym = Path(f).stem.split("_")[-1]
        for t in d["trades"]:
            sigs[(sym, t["entry_ts"], t["setup"], t["side"])].append(t)
    by_setup = collections.defaultdict(list)
    for k, lots in sigs.items():
        usd = sum(t["net_brl"] for t in lots)
        if usd < 0:
            by_setup[k[2]].append((k, lots, usd))
    chosen = {}
    for setup, cands in by_setup.items():
        full = [c for c in cands if all(t["exit_reason"] in ("stop", "stop_gap") for t in c[1])] or cands
        full.sort(key=lambda c: c[1][0]["risk_points"] / max(c[1][0]["entry_price"], 1e-9))
        chosen[setup] = full[len(full) // 2]  # exemplo típico: risco mediano entre as perdas cheias
    return chosen, {s: len(v) for s, v in by_setup.items()}


def plot(key, lots, usd, bars, idx, out: Path, n_losers: int):
    sym, ets, setup, side = key
    j = idx[ets]
    i = j - 1
    exit_j = max(idx.get(_bar_of(t["exit_ts"], bars, idx), j) for t in lots)
    a, b = max(0, i - 40), min(len(bars), exit_j + 10)
    win = bars[a:b]
    fig, ax = plt.subplots(figsize=(13, 6.5), dpi=110)
    ema, k = None, 2 / 21
    emas = []
    for bb in bars[max(0, a - 60):b]:
        ema = bb.close if ema is None else ema + k * (bb.close - ema)
        emas.append(ema)
    emas = emas[-len(win):]
    for x, bb in enumerate(win):
        up = bb.close >= bb.open
        col = "#2e7d32" if up else "#c62828"
        ax.plot([x, x], [bb.low, bb.high], color="#333", lw=0.8, zorder=2)
        body_lo, body_h = min(bb.open, bb.close), abs(bb.close - bb.open) or (bb.high - bb.low) * 0.02
        ax.add_patch(Rectangle((x - 0.35, body_lo), 0.7, body_h, facecolor=col if not up else "white",
                               edgecolor=col, lw=1.2, zorder=3))
    ax.plot(range(len(win)), emas, color="#f9a825", lw=1.4, label="EMA 20", zorder=1)
    si, ei = i - a, j - a
    sb = bars[i]
    ax.axvspan(si - 0.5, si + 0.5, color="#90caf9", alpha=0.35, zorder=0)
    ax.annotate("barra de sinal", (si, sb.high), xytext=(si - 6, sb.high + (sb.high - sb.low) * 2.2),
                arrowprops=dict(arrowstyle="->", color="#1565c0"), color="#1565c0", fontsize=9)
    t0 = lots[0]
    xe = len(win) - 1
    ax.hlines(t0["entry_price"], si, xe, colors="#1565c0", linestyles="-", lw=1.1, label=f"entrada {t0['entry_price']}")
    ax.hlines(t0["stop_price"], si, xe, colors="#c62828", linestyles="--", lw=1.1, label=f"stop {t0['stop_price']}")
    for t in lots:
        ax.hlines(t["target_price"], si, xe, colors="#2e7d32", linestyles=":", lw=1.1,
                  label=f"alvo {t['lot']} {t['target_price']}")
    ax.scatter([ei], [t0["entry_price"]], marker="^" if side == "long" else "v", s=110, color="#1565c0", zorder=5)
    for t in lots:
        xj = idx.get(_bar_of(t["exit_ts"], bars, idx))
        if xj is not None:
            ax.scatter([xj - a], [t["exit_price"]], marker="X", s=110, color="#c62828", zorder=5)
    step = max(1, len(win) // 12)
    ax.set_xticks(range(0, len(win), step))
    ax.set_xticklabels([win[x].ts_open.strftime("%d/%m %H:%M") for x in range(0, len(win), step)], rotation=30, fontsize=8)
    risk = t0["risk_points"]
    mfe = max(t["mfe_points"] for t in lots) / risk if risk else 0
    reasons = ", ".join(f"{t['lot']}: {REASON.get(t['exit_reason'], t['exit_reason'])} em {t['bars_held']} candle(s)" for t in lots)
    title = (f"{setup} — {SETUP_NAMES[setup]} | {sym} {'COMPRA' if side == 'long' else 'VENDA'} "
             f"{bars[j].ts_open:%d/%m/%Y %H:%M} (Brasília)")
    ax.set_title(title, fontsize=12, loc="left")
    txt = (f"Resultado: US$ {usd:+.2f}  ({usd / 40:+.2f}R)   |   {reasons}\n"
           f"Máx. a favor antes da saída: {mfe:.2f}R   |   regime na barra de sinal: {t0.get('regime')}   |   "
           f"perdas deste setup no período: {n_losers}")
    ax.text(0.01, -0.2, txt, transform=ax.transAxes, fontsize=9, va="top")
    ax.legend(loc="upper left", fontsize=8, framealpha=0.85)
    ax.grid(alpha=0.2)
    fig.subplots_adjust(bottom=0.24, left=0.07, right=0.98, top=0.93)
    fn = out / f"{int(setup[1:]):02d}_{setup}_{sym}.png"
    fig.savefig(fn)
    plt.close(fig)
    return fn


def _bar_of(exit_ts, bars, idx):
    # exit_ts é o fechamento do candle de saída; o candle abre 5 min antes
    from datetime import datetime, timedelta
    t = datetime.fromisoformat(exit_ts) - timedelta(minutes=5)
    return t.isoformat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", default="reports/losers")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    chosen, counts = pick_examples(a.files)
    cache = {}
    for setup in sorted(chosen, key=lambda s: int(s[1:])):
        key, lots, usd = chosen[setup]
        sym = key[0]
        if sym not in cache:
            bs = sorted(import_bars(str(RAW / f"{sym}_M5.csv"), sym, sym, server_offsets=OFFS.get(sym)).bars,
                        key=lambda b: b.ts_open)
            cache[sym] = (bs, {b.ts_open.isoformat(): k for k, b in enumerate(bs)})
        bs, idx = cache[sym]
        print(plot(key, sorted(lots, key=lambda t: t["lot"]), usd, bs, idx, out, counts[setup]))


if __name__ == "__main__":
    main()
