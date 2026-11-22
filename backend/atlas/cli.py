"""CLI do ATLAS B3.

    python -m atlas.cli import ARQUIVO --symbol WIN --contract WINJ26 [--timestamp-label open|close] [--resample-from-1m]
    python -m atlas.cli backtest --symbol WIN --contract WINJ26 [--profile scalp|daytrade] [--out relatorio.json]
    python -m atlas.cli synthetic --symbol WIN --contract WINJ26 --sessions 40   # DADOS SINTÉTICOS (teste)
    python -m atlas.cli datasets
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from atlas.config import get_settings
from atlas.persistence.repository import Repository


def _repo() -> Repository:
    s = get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    r = Repository(s.database_url)
    r.create_all()
    return r


def cmd_import(a) -> int:
    from atlas.market_data.importers import import_bars
    res = import_bars(a.file, a.symbol, a.contract, timestamp_label=a.timestamp_label, resample_from_1m=a.resample_from_1m)
    saved = _repo().save_import(res, a.file, replace=a.replace) if res.bars else {"inserted": 0}
    print(json.dumps({"summary": res.summary(), "saved": saved}, indent=2, ensure_ascii=False, default=str))
    for i in res.report.issues[:30]:
        print(f"[{i.severity}] {i.kind}: {i.message} {i.ts or ''}")
    return 0 if res.bars else 1


def cmd_backtest(a) -> int:
    from atlas.backtest.costs import CostModel
    from atlas.backtest.engine import run_backtest
    from atlas.reporting.serialize import json_safe
    from atlas.strategy.brooks_method import BrooksMethodParams
    from atlas.strategy.h2l2_momentum import H2L2MomentumParams
    from atlas.strategy.second_entry import SecondEntryParams
    bars = _repo().load_bars(a.symbol, a.contract)
    if not bars:
        print("Nenhum candle armazenado para esse contrato.", file=sys.stderr)
        return 1
    costs = CostModel(brokerage_per_contract_side=a.brokerage, exchange_fees_per_contract_side=a.fees, contracts=a.contracts,
                      risk_per_trade=a.risk, spread_ticks=a.spread_ticks)
    if a.strategy == "A":
        params = SecondEntryParams.for_profile(a.profile)
    elif a.strategy == "B":
        params = H2L2MomentumParams(stop_adr_frac=a.stop_adr_frac)
    else:
        params = BrooksMethodParams()
    r = json_safe(run_backtest(bars, a.symbol, params, costs, min_sample=a.min_sample))
    s = r["stats"]
    for w in r["warnings"]:
        print("AVISO:", w)
    for k in ("all", "train", "test"):
        x = s[k]
        print(f"{k:5s} n={x['n']:4d} acerto={x['win_rate']} expectativa_R={x['expectancy_r']} líquido=R${x['net_brl']} "
              f"PF={x['profit_factor']} DD=R${x['max_drawdown_brl']} {x['message'] or ''}")
    print(r["disclaimer"])
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=2)
        print("Relatório salvo em", a.out)
    return 0


def cmd_synthetic(a) -> int:
    from atlas.market_data.importers import ImportResult
    from atlas.market_data.synthetic import generate_bars
    from atlas.market_data.validation import ValidationReport
    label = a.contract.upper().removesuffix("_SINTETICO") + "_SINTETICO"
    bars = [b.with_(contract=label) for b in generate_bars(a.symbol, label, date.fromisoformat(a.start), a.sessions, a.seed)]
    rep = ValidationReport(a.symbol, label, rows_in=len(bars), rows_valid=len(bars))
    res = ImportResult(bars, rep, "synthetic", ["DADOS SINTÉTICOS"], f"synthetic-{a.seed}", f"SYNTHETIC:seed={a.seed}", True)
    print(json.dumps(_repo().save_import(res, "synthetic", replace=True)), "contrato:", label)
    print("ATENÇÃO: dados SINTÉTICOS — servem apenas para testar o software.")
    return 0


def cmd_datasets(a) -> int:
    for d in _repo().list_datasets():
        print(d)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="atlas", description="ATLAS B3 — pesquisa e simulação (não executa ordens)")
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("import")
    i.add_argument("file")
    i.add_argument("--symbol", required=True, choices=["WIN", "WDO", "EURUSD", "USDJPY"])
    i.add_argument("--contract", required=True)
    i.add_argument("--timestamp-label", default="open", choices=["open", "close"])
    i.add_argument("--resample-from-1m", action="store_true")
    i.add_argument("--replace", action="store_true")
    i.set_defaults(fn=cmd_import)
    b = sub.add_parser("backtest")
    b.add_argument("--symbol", required=True, choices=["WIN", "WDO", "EURUSD", "USDJPY"])
    b.add_argument("--contract", required=True)
    b.add_argument("--strategy", default="A", choices=["A", "B", "C"], help="A=Second Entry, B=usuário, C=Método Brooks")
    b.add_argument("--profile", default="daytrade", choices=["scalp", "daytrade"])
    b.add_argument("--risk", type=float, default=None, help="Risco fixo por operação na moeda da conta (ex.: 40)")
    b.add_argument("--spread-ticks", type=float, default=0.0, help="Meio spread por lado, em ticks")
    b.add_argument("--stop-adr-frac", type=float, default=None, help="Estratégia B: stop como fração do range diário")
    b.add_argument("--brokerage", type=float, default=0.0)
    b.add_argument("--fees", type=float, default=0.0)
    b.add_argument("--contracts", type=float, default=1)
    b.add_argument("--min-sample", type=int, default=30)
    b.add_argument("--out")
    b.set_defaults(fn=cmd_backtest)
    s = sub.add_parser("synthetic")
    s.add_argument("--symbol", default="WIN", choices=["WIN", "WDO"])
    s.add_argument("--contract", default="WINJ26")
    s.add_argument("--start", default="2026-01-02")
    s.add_argument("--sessions", type=int, default=40)
    s.add_argument("--seed", type=int, default=7)
    s.set_defaults(fn=cmd_synthetic)
    d = sub.add_parser("datasets")
    d.set_defaults(fn=cmd_datasets)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
