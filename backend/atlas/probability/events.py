"""Estimativa de probabilidade de EVENTOS DEFINIDOS a partir de operações hipotéticas.

Evento padrão: "atingir o alvo de ``target_r``R antes do stop de 1R, em até
``time_exit_bars`` candles de 5 minutos após a entrada" para uma estratégia e
ativo. Estimador: frequência empírica no conjunto de TREINO com IC de Wilson;
avaliação fora da amostra pelo Brier score contra a taxa observada no TESTE.
"""
from __future__ import annotations

from dataclasses import dataclass

from atlas.probability.stats import DEFAULT_MIN_SAMPLE, INSUFFICIENT_MSG, brier_score, wilson_interval


@dataclass
class EventProbability:
    event: str
    method: str
    n_train: int
    hits_train: int
    p_hat: float | None
    ci95: tuple[float, float] | None
    n_test: int
    hits_test: int
    test_rate: float | None
    brier_test: float | None
    brier_reference: float | None  # Brier da taxa do teste (oráculo) — limite inferior para preditor constante
    sufficient: bool
    message: str | None

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def describe_event(symbol: str, strategy_id: str, target_r: float, time_exit_bars: int) -> str:
    return (f"Probabilidade estimada de uma operação hipotética da estratégia '{strategy_id}' em {symbol} atingir "
            f"o alvo de {target_r:g}R antes do stop de 1R, em até {time_exit_bars} candles de 5 minutos após a entrada "
            "(saídas por tempo, sessão ou lacuna contam como não-ocorrência).")


def estimate_target_first(train: list[dict], test: list[dict], event: str, min_sample: int = DEFAULT_MIN_SAMPLE) -> EventProbability:
    def hit(t: dict) -> int:
        return 1 if t["exit_reason"] in ("target", "target_gap") else 0

    h_tr = sum(hit(t) for t in train)
    h_te = sum(hit(t) for t in test)
    n_tr, n_te = len(train), len(test)
    sufficient = n_tr >= min_sample
    p_hat = h_tr / n_tr if (n_tr and sufficient) else None
    ci = wilson_interval(h_tr, n_tr) if (n_tr and sufficient) else None
    test_rate = h_te / n_te if n_te else None
    brier = brier_ref = None
    if p_hat is not None and n_te:
        outcomes = [hit(t) for t in test]
        brier = brier_score([p_hat] * n_te, outcomes)
        brier_ref = brier_score([test_rate] * n_te, outcomes)
    msg = None
    if not sufficient:
        msg = f"{INSUFFICIENT_MSG} (n_treino={n_tr} < {min_sample})"
    elif n_te < min_sample:
        msg = f"Estimativa de treino disponível, mas validação fora da amostra com amostra pequena (n_teste={n_te})."
    return EventProbability(event, "frequência empírica (treino) + IC Wilson 95%; Brier no teste", n_tr, h_tr, p_hat, ci,
                            n_te, h_te, test_rate, brier, brier_ref, sufficient, msg)
