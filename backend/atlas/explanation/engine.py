"""Explanation Engine — explicações determinísticas em português (seções A–G).

Regra: todo número exibido vem do ``payload`` calculado pelos motores. Este
módulo apenas formata; não cria preços, probabilidades, notícias ou citações.
"""
from __future__ import annotations

from atlas.knowledge.catalog import REFERENCES, get_concept

REGIME_PT = {
    "BULL_TREND": "tendência de alta", "BEAR_TREND": "tendência de baixa", "TRADING_RANGE": "trading range",
    "TIGHT_TRADING_RANGE": "trading range estreito", "UNDEFINED": "indefinido (dados insuficientes)",
}


def _f(x, nd=2, pct=False):
    if x is None:
        return "n/d"
    if isinstance(x, (list, tuple)):
        return "–".join(_f(v, nd, pct) for v in x)
    if pct:
        return f"{x * 100:.1f}%".replace(".", ",")
    if isinstance(x, float):
        return f"{x:,.{nd}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return str(x)


def explain_alert(payload: dict) -> dict:
    side = payload.get("side")
    lado = "compra" if side == "long" else "venda"
    reg = payload.get("regime", {})
    sig = payload.get("signal", {})
    setup = payload.get("setup", "")
    criteria = payload.get("criteria", [])
    ev = payload.get("evidence") or {}

    a = {
        "title": "A. Diagnóstico do mercado",
        "text": (f"Regime identificado: {REGIME_PT.get(reg.get('regime'), reg.get('regime'))}. "
                 f"{_f(reg.get('frac_above_ema'), pct=True)} dos últimos fechamentos acima da EMA20; inclinação da EMA de "
                 f"{_f(reg.get('ema_slope_atr'))} ATR em 5 candles; eficiência direcional {_f(reg.get('efficiency'))}; "
                 f"sobreposição média {_f(reg.get('overlap_mean'))}. Viés 'Always In' aproximado: {reg.get('always_in')}."),
    }
    concepts = [get_concept(c) for c in ("trend_vs_range", "high1_high2", "second_entry", "signal_bar")]
    b = {
        "title": "B. Interpretação segundo Al Brooks",
        "text": (f"Configuração de segunda entrada ({setup}) a favor da tendência. As ideias abaixo são resumos do ATLAS "
                 "sobre conceitos de Brooks (não são citações literais), seguidos de como o ATLAS os implementa."),
        "items": [{"concept": c.name, "author_concept": c.author_concept, "atlas_interpretation": c.atlas_interpretation,
                   "quant_rule": c.quant_rule,
                   "references": [f"{REFERENCES[r].author}, {REFERENCES[r].title} ({REFERENCES[r].edition})" for r in c.references]}
                  for c in concepts if c],
    }
    c = {
        "title": "C. Evidências técnicas (critérios objetivos)",
        "items": [{"code": x["code"], "description": x["description"], "passed": x["passed"],
                   "value": x["value"], "threshold": x["threshold"]} for x in criteria],
        "text": f"{sum(1 for x in criteria if x['passed'])} de {len(criteria)} critérios atendidos.",
    }
    macro = payload.get("macro", {})
    d = {"title": "D. Contexto macroeconômico",
         "text": macro.get("note", "Contexto macroeconômico não verificado.")}
    if ev and ev.get("stats"):
        st = ev["stats"]
        prob = ev.get("probability") or {}
        text = (f"Fonte: backtest {ev.get('run_id')} ({ev.get('period')}), mesma estratégia/versão/parâmetros. "
                f"Amostra: {st.get('n')} operações; taxa de acerto {_f(st.get('win_rate'), pct=True)} "
                f"(IC95% {_f(st.get('win_rate_ci95'), pct=True)}); expectativa {_f(st.get('expectancy_r'))}R líquido "
                f"(IC95% {_f(st.get('expectancy_r_ci95'))}); profit factor {_f(st.get('profit_factor'))}.")
        if not st.get("sufficient"):
            text += " " + (st.get("message") or "")
        if prob.get("p_hat") is not None:
            text += (f" Evento: {prob.get('event')} Estimativa de treino {_f(prob.get('p_hat'), pct=True)} "
                     f"(IC95% {_f(prob.get('ci95'), pct=True)}, n={prob.get('n_train')}); taxa fora da amostra "
                     f"{_f(prob.get('test_rate'), pct=True)} (n={prob.get('n_test')}).")
        elif prob.get("message"):
            text += " " + prob["message"]
        if ev.get("synthetic"):
            text += " ATENÇÃO: o backtest de referência usou DADOS SINTÉTICOS e não tem valor estatístico sobre o mercado."
    else:
        text = "Evidência estatística insuficiente para estimar uma probabilidade confiável. Nenhum backtest compatível foi associado a este alerta."
    e = {"title": "E. Evidência quantitativa", "text": text}
    f = {
        "title": "F. Cenário hipotético",
        "text": (f"Confirmação: o candle seguinte {'ultrapassar' if side == 'long' else 'perder'} {_f(sig.get('entry_price'))} "
                 f"(ordem stop de {lado} hipotética, válida só no próximo candle de 5 min). Stop técnico em "
                 f"{_f(sig.get('stop_price'))} ({_f(sig.get('risk_ticks'), 0)} ticks); alvo em {_f(sig.get('target_price'))} "
                 f"({_f(sig.get('target_r'))}R; relação retorno/risco efetiva {_f(sig.get('reward_risk'))}). Saída por tempo após "
                 f"{sig.get('time_exit_bars')} candles. Invalidação: ordem não acionada no candle seguinte, lacuna de dados, "
                 "ou toque no stop."),
    }
    lim = ["A classificação de regime é uma aproximação quantitativa da leitura contextual de Brooks.",
           "Sem dados intrabar, stop e alvo no mesmo candle são resolvidos como stop (conservador).",
           "Fluxo de ordens (agressão, livro) não é utilizado: os dados são apenas OHLCV.",
           "Contexto macroeconômico não integrado."]
    if payload.get("synthetic_data"):
        lim.insert(0, "ESTE ALERTA FOI GERADO SOBRE DADOS SINTÉTICOS (não reais).")
    g = {"title": "G. Limitações", "items": lim}
    return {"language": "pt-BR", "generator": "deterministic-v1", "sections": [a, b, c, d, e, f, g]}
