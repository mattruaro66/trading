"""Validação de conceitos de contexto de Al Brooks (pesquisa; não altera estratégias).

Uso:
    python scripts/research_brooks_context.py --symbol EURUSD --csv data/raw/EURUSD_M5.csv --out saida.json
    python scripts/research_brooks_context.py --report saida_EURUSD.json saida_USDJPY.json ...

Hipóteses e critérios FIXADOS ANTES de rodar (não mudar depois de ver o resultado):

H1 Ciclo de mercado. Fase em cada candle (só dados passados):
   SPIKE   = 2 trend bars consecutivas na mesma direção (corpo ≥ 50%, amplitude ≥ 1 ATR) com fechamento além
             do extremo das 20 barras anteriores, na barra atual ou na anterior;
   CANAL ESTREITO = |inclinação da EMA20 em 10 barras| ≥ 1 ATR e nenhum fechamento do outro lado da EMA em 10 barras;
   CANAL AMPLO    = |inclinação| ≥ 0,5 ATR;      LATERAL = o resto.
   Previsão: entradas a favor (estratégias B e D) em SPIKE/CANAL ESTREITO alinhados > em CANAL AMPLO/LATERAL.
H2 TBTL. Clímax = barra com amplitude ≥ 2,5 ATR, corpo ≥ 50%, fechando além do extremo de 20 barras.
   Previsão a: entradas a favor com clímax a favor nas 10 barras anteriores < entradas sem clímax recente.
   Previsão b: H2/L2/H3/L3 após clímax a favor (até 60 barras) com ≥ 10 barras de correção > com < 10 barras.
H3 Pernas na lateralidade. Em fase LATERAL, preço no terço/quinto extremo da faixa de 40 barras, movimento
   desde o último pivô oposto com 2–3 pernas, barra de sinal contrária fechando na metade contrária:
   ordem stop 1 tick além da barra de sinal (válida 1 candle), stop 1 tick além do outro extremo (mín. 1 ATR),
   alvo 1R (scalp de range). Previsão: expectativa > 0 e reversões com 2–3 pernas > com 1 perna.
H4 Suporte/resistência. Níveis: máx/mín/fech. do dia anterior, máx/mín do dia até a barra anterior,
   pivôs (2 barras de cada lado) das últimas 300 barras, números redondos.
   Previsão a: espaço até o próximo nível ≥ 2R > espaço < 1R.
   Previsão b: barra de sinal que rompe um nível (fecha além, anterior não) > sem rompimento.

Critério de confirmação (cada previsão): diferença na direção prevista com t ≥ 2,6 (≈ Bonferroni para 7
comparações), mesma direção na amostra de treino (70% iniciais) e de teste (30% finais) e em ≥ 2 de 3 ativos.
Execução sem custos (pedido do usuário); H3 também com 0,5 pip de spread. Candle com stop e alvo = stop.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROUND_STEP = {"EURUSD": 0.005, "USDJPY": 0.5, "USDBRL": 0.05, "WIN": 1000.0, "WDO": 50.0}
PIP = {"EURUSD": 0.0001, "USDJPY": 0.01, "USDBRL": 0.0001, "WIN": 5.0, "WDO": 0.5}


# ----------------------------------------------------------------------------- séries
def frame(bars) -> pd.DataFrame:
    df = pd.DataFrame({"ts": [b.ts_open.isoformat() for b in bars], "o": [b.open for b in bars],
                       "h": [b.high for b in bars], "l": [b.low for b in bars], "c": [b.close for b in bars],
                       "day": [b.ts_open.date() for b in bars]})
    tr = np.maximum(df.h - df.l, np.maximum((df.h - df.c.shift()).abs(), (df.l - df.c.shift()).abs()))
    df["atr"] = tr.rolling(14).mean()
    df["ema"] = df.c.ewm(span=20, adjust=False).mean()
    rng = (df.h - df.l).replace(0, np.nan)
    body = (df.c - df.o).abs() / rng
    d = np.sign(df.c - df.o)
    prev_atr = df.atr.shift()
    hi20, lo20 = df.h.shift().rolling(20).max(), df.l.shift().rolling(20).min()
    trend_bar = (body >= 0.5) & ((df.h - df.l) >= prev_atr)
    beyond = ((d > 0) & (df.c > hi20)) | ((d < 0) & (df.c < lo20))
    pair = trend_bar & trend_bar.shift().fillna(False).astype(bool) & (d == d.shift())
    df["spike_dir"] = np.where(pair & beyond, d, 0)
    df["climax_dir"] = np.where((df.h - df.l >= 2.5 * prev_atr) & (body >= 0.5) & beyond, d, 0)
    slope = (df.ema - df.ema.shift(10)) / df.atr
    above = (df.c > df.ema).astype(int).rolling(10).sum()
    sp = pd.Series(df.spike_dir).replace(0, np.nan).ffill(limit=1).fillna(0)
    phase = np.where(sp != 0, "SPIKE",
             np.where((slope.abs() >= 1.0) & (((slope > 0) & (above == 10)) | ((slope < 0) & (above == 0))), "CANAL_ESTREITO",
             np.where(slope.abs() >= 0.5, "CANAL_AMPLO", "LATERAL")))
    df["phase"] = phase
    df["phase_dir"] = np.where(sp != 0, sp, np.sign(slope.fillna(0)))
    h, l = df.h.values, df.l.values
    n = len(df)
    ph = np.zeros(n, bool); pl = np.zeros(n, bool)
    ph[2:-2] = (h[2:-2] > h[1:-3]) & (h[2:-2] > h[:-4]) & (h[2:-2] >= h[3:-1]) & (h[2:-2] >= h[4:])
    pl[2:-2] = (l[2:-2] < l[1:-3]) & (l[2:-2] < l[:-4]) & (l[2:-2] <= l[3:-1]) & (l[2:-2] <= l[4:])
    df["piv_h"], df["piv_l"] = ph, pl  # confirmado só em k+2 (usado com esse atraso)
    df["last_of_day"] = df.day != df.day.shift(-1)
    return df


def welch(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 5 or len(b) < 5:
        return None
    se = math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return (a.mean() - b.mean()) / se if se else None


# ----------------------------------------------------------------------------- features por operação
def trade_features(df: pd.DataFrame, trades: list[dict], sym: str) -> list[dict]:
    idx = {t: i for i, t in enumerate(df.ts)}
    h, l, c, atr = df.h.values, df.l.values, df.c.values, df.atr.values
    day = df.day.values
    g = df.groupby("day").agg(h=("h", "max"), l=("l", "min"), c=("c", "last"))
    prev = {d: tuple(g.iloc[k - 1]) for k, d in enumerate(g.index) if k > 0}
    first = df.reset_index().groupby("day")["index"].min().to_dict()
    piv_idx = np.where(df.piv_h.values | df.piv_l.values)[0]
    step = ROUND_STEP[sym]
    clim = df.climax_dir.values
    out = []
    for t in trades:
        j = idx.get(t["entry_ts"])
        if j is None or j < 320 or day[j - 1] not in prev:
            continue
        i = j - 1
        s = 1 if t["side"] == "long" else -1
        entry, risk = t["entry_price"], t["risk_points"]
        lv = list(prev[day[i]])
        f0 = first[day[i]]
        if i > f0:
            lv += [h[f0:i].max(), l[f0:i].min()]
        ks = piv_idx[(piv_idx >= i - 300) & (piv_idx <= i - 2)]
        lv += [h[k] for k in ks if df.piv_h.values[k]] + [l[k] for k in ks if df.piv_l.values[k]]
        base = round(entry / step) * step
        lv += [base + step * m for m in (-2, -1, 0, 1, 2)]
        lv = np.array(lv)
        ahead = lv[(lv - entry) * s > 0]
        room = float(np.min(np.abs(ahead - entry)) / risk) if len(ahead) and risk else 99.0
        broke = bool(np.any(((c[i] - lv) * s > 0) & ((c[i - 1] - lv) * s <= 0)))
        recent = clim[i - 10:i]
        since = None
        for k in range(i - 1, i - 61, -1):
            if clim[k] == s:
                since = i - k
                break
        out.append(dict(setup=t["setup"], side=t["side"], net_r=t["net_r"], entry_ts=t["entry_ts"],
                        phase=df.phase.values[i], aligned=bool(df.phase_dir.values[i] == s),
                        climax10=bool((recent == s).any()), bars_since_climax=since, room_r=room, broke=broke))
    return out


# ----------------------------------------------------------------------------- H3: pernas na lateralidade
def range_leg_fades(df: pd.DataFrame, sym: str, tick: float) -> list[dict]:
    h, l, o, c, atr = df.h.values, df.l.values, df.o.values, df.c.values, df.atr.values
    ph, pl, phase, last, day = df.piv_h.values, df.piv_l.values, df.phase.values, df.last_of_day.values, df.day.values
    out, busy_until = [], -1
    for i in range(60, len(df) - 1):
        if i <= busy_until or phase[i] != "LATERAL" or last[i] or not atr[i] > 0:
            continue
        hi40, lo40 = h[i - 39:i + 1].max(), l[i - 39:i + 1].min()
        width = hi40 - lo40
        if width <= 0:
            continue
        for s in (-1, 1):  # s = lado da operação (−1: venda no topo; +1: compra no fundo)
            opp = pl if s < 0 else ph  # pivô onde o movimento começou
            same = ph if s < 0 else pl
            ks = [k for k in range(i - 40, i - 1) if opp[k]]
            if not ks:
                continue
            k0 = ks[-1]
            seg = h[k0:i + 1] if s < 0 else l[k0:i + 1]
            ext = seg.max() if s < 0 else seg.min()
            near_ext = (h[i] >= ext - 0.25 * atr[i]) if s < 0 else (l[i] <= ext + 0.25 * atr[i])
            in_zone = (ext >= hi40 - 0.2 * width) if s < 0 else (ext <= lo40 + 0.2 * width)
            sig_ok = (c[i] < o[i] and c[i] < (h[i] + l[i]) / 2) if s < 0 else (c[i] > o[i] and c[i] > (h[i] + l[i]) / 2)
            if not (near_ext and in_zone and sig_ok):
                continue
            legs = 1
            for k in range(k0 + 1, i - 1):
                if same[k] and k + 2 <= i:
                    later = h[k + 1:i + 1].max() if s < 0 else l[k + 1:i + 1].min()
                    if (later > h[k]) if s < 0 else (later < l[k]):
                        legs += 1
            entry = (l[i] - tick) if s < 0 else (h[i] + tick)
            stop = (h[i] + tick) if s < 0 else (l[i] - tick)
            risk = max(abs(stop - entry), atr[i])
            stop = entry - s * risk
            target = entry + s * risk  # 1R
            j = i + 1
            if day[j] != day[i]:
                continue
            filled = (l[j] <= entry) if s < 0 else (h[j] >= entry)
            if not filled:
                continue
            fill = min(entry, o[j]) if s < 0 else max(entry, o[j])
            r = None
            for m in range(j, len(df)):
                hit_stop = (h[m] >= stop) if s < 0 else (l[m] <= stop)
                hit_tgt = (l[m] <= target) if s < 0 else (h[m] >= target)
                if hit_stop:
                    exit_p = max(stop, o[m]) if (s < 0 and m > j) else (min(stop, o[m]) if m > j else stop)
                    r = s * (exit_p - fill) / risk
                    break
                if hit_tgt:
                    r = s * (target - fill) / risk
                    break
                if last[m]:
                    r = s * (c[m] - fill) / risk
                    break
            if r is None:
                continue
            busy_until = m
            out.append(dict(setup="RANGE_FADE", side="long" if s > 0 else "short", entry_ts=df.ts.values[j],
                            net_r=float(r), legs=int(legs), risk=float(risk)))
            break
    return out


# ----------------------------------------------------------------------------- execução por ativo
def run_symbol(sym: str, csv: str, offsets, out: str) -> None:
    from atlas.backtest.costs import CostModel
    from atlas.backtest.engine import run_backtest
    from atlas.contracts.specs import get_spec
    from atlas.market_data.importers import import_bars
    from atlas.reporting.serialize import json_safe
    from atlas.strategy.h2l2_momentum import H2L2MomentumParams

    bars = sorted(import_bars(csv, sym, sym, server_offsets=offsets).bars, key=lambda b: b.ts_open)
    df = frame(bars)
    costs = CostModel(risk_per_trade=40, spread_ticks=0, entry_slippage_ticks=0, exit_slippage_ticks=0,
                      target_slippage_ticks=0)
    configs = {"B": H2L2MomentumParams(stop_adr_frac=0.10),
               "D": H2L2MomentumParams(stop_adr_frac=0.10, enable_legs=False, enable_momentum=False, min_stop_atr=1.0,
                                       min_stop_action="widen", tech_target_r=2.0)}
    res = {"symbol": sym, "cut": None, "sets": {}}
    for name, p in configs.items():
        r = json_safe(run_backtest(bars, sym, p, costs))
        res["cut"] = r["meta"]["train_test_cutoff"]
        res["sets"][name] = trade_features(df, r["trades"], sym)
    fades = range_leg_fades(df, sym, get_spec(sym).tick_size)
    pip = PIP[sym]
    for f in fades:
        f["net_r_spread"] = f["net_r"] - 0.5 * pip / f["risk"]
    res["sets"]["H3"] = fades
    Path(out).write_text(json.dumps(res, default=str))
    print(sym, {k: len(v) for k, v in res["sets"].items()})


# ----------------------------------------------------------------------------- relatório
def report(files: list[str]) -> dict:
    data = [json.loads(Path(f).read_text()) for f in files]

    def rows(name):
        out = []
        for d in data:
            for t in d["sets"][name]:
                out.append({**t, "sym": d["symbol"], "train": t["entry_ts"] < d["cut"]})
        return pd.DataFrame(out)

    tests = []

    def compare(label, df, good, bad, col="net_r"):
        rec = {"hipotese": label}
        for part, m in (("total", np.ones(len(df), bool)), ("treino", df.train.values), ("teste", ~df.train.values)):
            a, b = df[col][good & m], df[col][bad & m]
            rec[part] = {"n_bom": int(len(a)), "R_bom": round(a.mean(), 3) if len(a) else None,
                         "n_ruim": int(len(b)), "R_ruim": round(b.mean(), 3) if len(b) else None,
                         "dif": round(a.mean() - b.mean(), 3) if len(a) and len(b) else None, "t": welch(a, b)}
        rec["por_ativo"] = {s: (round(df[col][good & (df.sym == s)].mean() - df[col][bad & (df.sym == s)].mean(), 3)
                                if (good & (df.sym == s)).any() and (bad & (df.sym == s)).any() else None)
                            for s in sorted(df.sym.unique())}
        t = rec["total"]["t"]
        pos = sum(1 for v in rec["por_ativo"].values() if v is not None and v > 0)
        rec["confirmado"] = bool(t is not None and t >= 2.6 and (rec["treino"]["dif"] or 0) > 0
                                 and (rec["teste"]["dif"] or 0) > 0 and pos >= 2)
        tests.append(rec)

    for name in ("B", "D"):
        x = rows(name)
        if x.empty:
            continue
        strong = x.aligned & x.phase.isin(["SPIKE", "CANAL_ESTREITO"])
        weak = x.phase.isin(["CANAL_AMPLO", "LATERAL"])
        compare(f"H1 ciclo [{name}]: spike/canal estreito a favor > canal amplo/lateral", x, strong.values, weak.values)
        compare(f"H2a TBTL [{name}]: sem clímax recente > com clímax a favor nas 10 barras", x,
                (~x.climax10).values, x.climax10.values)
        compare(f"H4a S/R [{name}]: espaço ≥ 2R > espaço < 1R", x, (x.room_r >= 2).values, (x.room_r < 1).values)
        compare(f"H4b S/R [{name}]: rompe nível > não rompe", x, x.broke.values, (~x.broke).values)
    xb = rows("B")
    legs = xb[xb.setup.isin(["H2", "L2", "H3", "L3"]) & xb.bars_since_climax.notna()]
    if not legs.empty:
        compare("H2b TBTL [B pernas]: correção ≥ 10 barras após clímax > < 10 barras", legs,
                (legs.bars_since_climax >= 10).values, (legs.bars_since_climax < 10).values)
    f = rows("H3")
    h3 = {}
    if not f.empty:
        compare("H3 lateralidade: reversão após 2–3 pernas > após 1 perna", f,
                f.legs.between(2, 3).values, (f.legs == 1).values)
        sel = f[f.legs.between(2, 3)]
        for part, m in (("total", np.ones(len(sel), bool)), ("treino", sel.train.values), ("teste", ~sel.train.values)):
            v = sel.net_r[m]
            vs = sel.net_r_spread[m]
            h3[part] = {"n": int(len(v)), "R": round(v.mean(), 3) if len(v) else None,
                        "t": (v.mean() / (v.std(ddof=1) / math.sqrt(len(v)))) if len(v) > 5 else None,
                        "R_spread_0.5pip": round(vs.mean(), 3) if len(vs) else None,
                        "acerto": round((v > 0).mean(), 3) if len(v) else None}
        h3["por_ativo"] = {s: round(sel.net_r[sel.sym == s].mean(), 3) for s in sorted(sel.sym.unique())}
        h3["por_pernas"] = {int(k): {"n": int(len(v)), "R": round(v.mean(), 3)} for k, v in f.groupby("legs").net_r}
    phases = {}
    for name in ("B", "D"):
        x = rows(name)
        if not x.empty:
            phases[name] = {f"{p}{'' if p == 'LATERAL' else (' a favor' if a else ' contra')}":
                            {"n": int(len(v)), "R": round(v.net_r.mean(), 3)}
                            for (p, a), v in x.groupby(["phase", "aligned"])}
    return {"testes": tests, "H3_reversao_2_3_pernas": h3, "R_por_fase": phases}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol")
    ap.add_argument("--csv")
    ap.add_argument("--server-offsets", default=None)
    ap.add_argument("--out")
    ap.add_argument("--report", nargs="+")
    a = ap.parse_args()
    if a.report:
        print(json.dumps(report(a.report), ensure_ascii=False, indent=1, default=str))
        return 0
    offs = tuple(int(x) for x in a.server_offsets.split(",")) if a.server_offsets else None
    run_symbol(a.symbol, a.csv, offs, a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
