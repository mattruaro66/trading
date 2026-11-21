"""Timeframe operacional único do ATLAS: 5 minutos.

Não existe configuração para outros timeframes operacionais. Dados de maior
granularidade (1 min, negócios) só podem ser *agregados* para 5 min.
"""
from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

TIMEFRAME = timedelta(minutes=5)
TIMEFRAME_MINUTES = 5
TIMEFRAME_LABEL = "5min"
TZ = ZoneInfo("America/Sao_Paulo")


def assert_operational_timeframe(minutes: int) -> None:
    if minutes != TIMEFRAME_MINUTES:
        raise ValueError(
            f"Timeframe operacional de {minutes} min não é suportado: o ATLAS opera exclusivamente em 5 minutos."
        )
