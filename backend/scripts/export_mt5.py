"""Exporta candles de 5 minutos de WIN/WDO do MetaTrader 5 para CSV no formato do ATLAS B3.

Roda no Windows, com o MetaTrader 5 aberto e conectado ao servidor de uma corretora com acesso à B3.
Não envia ordens: só lê o histórico de preços.

Uso (Prompt de Comando, na pasta deste arquivo):
    pip install MetaTrader5 pandas
    python export_mt5.py --inicio 2023-01-01 --saida C:\\ATLAS_dados

Gera um CSV por símbolo encontrado (ex.: WINZ25_M5.csv, WIN$N_M5.csv) com as colunas
timestamp,open,high,low,close,volume,tick_volume,spread  (timestamp = ABERTURA do candle, horário do servidor).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    import MetaTrader5 as mt5
    import pandas as pd
except ImportError:
    sys.exit("Instale as dependências primeiro:  pip install MetaTrader5 pandas")


def main() -> int:
    ap = argparse.ArgumentParser(description="Exporta WIN/WDO em 5 min do MetaTrader 5 para o ATLAS B3")
    ap.add_argument("--prefixos", nargs="+", default=["WIN", "WDO"], help="Início dos nomes dos símbolos")
    ap.add_argument("--inicio", default="2023-01-01", help="Data inicial (AAAA-MM-DD)")
    ap.add_argument("--fim", default=None, help="Data final (padrão: hoje)")
    ap.add_argument("--saida", default="atlas_dados", help="Pasta de saída")
    ap.add_argument("--simbolos", nargs="*", default=None, help="Exportar só estes símbolos (opcional)")
    a = ap.parse_args()

    if not mt5.initialize():
        print("Não foi possível conectar ao MetaTrader 5:", mt5.last_error())
        print("Deixe o MT5 aberto e logado na conta da corretora (não só na conta MQL5/Gmail).")
        return 1
    term, acc = mt5.terminal_info(), mt5.account_info()
    print(f"Terminal: {term.name if term else '?'} | servidor: {acc.server if acc else 'sem conta logada'}")

    todos = mt5.symbols_get() or []
    nomes = [s.name for s in todos if any(s.name.upper().startswith(p) for p in a.prefixos)]
    if a.simbolos:
        nomes = [n for n in nomes if n in a.simbolos]
    if not nomes:
        print("Nenhum símbolo WIN/WDO encontrado. O servidor conectado provavelmente não oferece dados da B3.")
        mt5.shutdown()
        return 1
    print(f"{len(nomes)} símbolos encontrados:", ", ".join(sorted(nomes)))

    inicio = datetime.fromisoformat(a.inicio)
    fim = datetime.fromisoformat(a.fim) if a.fim else datetime.now() + timedelta(days=1)
    out = Path(a.saida)
    out.mkdir(parents=True, exist_ok=True)

    for nome in sorted(nomes):
        mt5.symbol_select(nome, True)
        partes, t = [], inicio
        while t < fim:  # em janelas de 30 dias para não estourar o limite de barras do terminal
            t2 = min(t + timedelta(days=30), fim)
            r = mt5.copy_rates_range(nome, mt5.TIMEFRAME_M5, t, t2)
            if r is not None and len(r):
                partes.append(pd.DataFrame(r))
            t = t2
        if not partes:
            print(f"  {nome}: sem histórico no período")
            continue
        df = pd.concat(partes).drop_duplicates("time").sort_values("time")
        df["timestamp"] = pd.to_datetime(df["time"], unit="s")  # horário do servidor (normalmente Brasília na B3)
        vol = df["real_volume"] if "real_volume" in df and df["real_volume"].sum() > 0 else pd.NA
        res = pd.DataFrame({"timestamp": df["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S"), "open": df["open"],
                            "high": df["high"], "low": df["low"], "close": df["close"], "volume": vol,
                            "tick_volume": df["tick_volume"], "spread": df["spread"]})
        arq = out / f"{nome}_M5.csv"
        res.to_csv(arq, index=False)
        primeiros = df.groupby(df["timestamp"].dt.date)["timestamp"].min().dt.strftime("%H:%M").value_counts().head(3)
        print(f"  {nome}: {len(res)} candles de {res['timestamp'].iloc[0]} a {res['timestamp'].iloc[-1]} → {arq}")
        print(f"     horário do 1º candle do dia (mais comuns): {dict(primeiros)}")

    mt5.shutdown()
    print("\nPronto. Anexe os CSVs (ou um .zip da pasta) no chat do ATLAS. Não publique os dados em repositório público.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
