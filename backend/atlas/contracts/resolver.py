"""ContractResolver — códigos de vencimento, datas de vencimento e rolagem.

O símbolo genérico ``WIN``/``WDO`` **não** é tratado como o contrato vigente.
A seleção de contrato é sempre explícita; ``suggest_front`` apenas *sugere* e
devolve a justificativa para auditoria.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from atlas.contracts.calendar import TradingCalendar, load_calendar
from atlas.contracts.specs import ContractSpec, get_spec, month_codes
from atlas.core.types import Symbol

_CODE_RE = re.compile(r"^(WIN|WDO)([FGHJKMNQUVXZ])(\d{2})$")


@dataclass(frozen=True)
class ContractInfo:
    code: str
    symbol: Symbol
    month: int
    year: int
    expiry_date: date
    last_trading_day: date
    rule: str
    calendar_coverage: bool  # False se o ano não está coberto pelo arquivo de feriados


@dataclass(frozen=True)
class RollPolicy:
    """Rolagem para o próximo vencimento ``days_before`` pregões antes do último dia de negociação."""

    days_before: int = 2
    description: str = "Rolar N pregões antes do último dia de negociação do contrato vigente."


@dataclass
class ContractSuggestion:
    contract: ContractInfo
    reason: str
    policy: RollPolicy
    alternatives: list[str] = field(default_factory=list)


class ContractResolver:
    def __init__(self, calendar: TradingCalendar | None = None):
        self.cal = calendar or load_calendar()
        self.codes = month_codes()
        self.code_by_month = {v: k for k, v in self.codes.items()}

    # ------------------------------------------------------------------
    def parse(self, code: str) -> ContractInfo:
        m = _CODE_RE.match(code.strip().upper())
        if not m:
            raise ValueError(
                f"Código de contrato inválido: {code!r}. Use o código completo (ex.: WINZ26, WDOF27); "
                "o símbolo genérico não identifica um vencimento."
            )
        sym, letter, yy = m.groups()
        spec = get_spec(sym)
        if letter not in spec.expiry_month_codes:
            raise ValueError(f"{sym} não possui vencimento no mês {letter} segundo a especificação vigente")
        month = self.codes[letter]
        year = 2000 + int(yy)
        expiry, last = self._expiry(spec, year, month)
        return ContractInfo(
            code=f"{sym}{letter}{yy}", symbol=Symbol(sym), month=month, year=year,
            expiry_date=expiry, last_trading_day=last, rule=spec.expiry_rule,
            calendar_coverage=self.cal.has_coverage(expiry),
        )

    def code_for(self, symbol: Symbol | str, year: int, month: int) -> str:
        return f"{Symbol(symbol).value}{self.code_by_month[month]}{year % 100:02d}"

    def _expiry(self, spec: ContractSpec, year: int, month: int) -> tuple[date, date]:
        if spec.expiry_rule == "wednesday_nearest_15th":
            d15 = date(year, month, 15)
            # quarta-feira (weekday 2) mais próxima do dia 15
            offset = 2 - d15.weekday()
            if offset > 3:
                offset -= 7
            elif offset < -3:
                offset += 7
            d = d15 + timedelta(days=offset)
            if not self.cal.is_trading_day(d):
                d = self.cal.next_trading_day(d)
            return d, d
        if spec.expiry_rule == "first_business_day":
            d = date(year, month, 1)
            if not self.cal.is_trading_day(d):
                d = self.cal.next_trading_day(d)
            return d, self.cal.prev_trading_day(d)
        raise ValueError(f"Regra de vencimento desconhecida: {spec.expiry_rule}")

    # ------------------------------------------------------------------
    def listed_after(self, symbol: Symbol | str, on: date, n: int = 4) -> list[ContractInfo]:
        """Próximos ``n`` contratos cujo último dia de negociação é >= ``on``."""
        spec = get_spec(symbol, on)
        out: list[ContractInfo] = []
        y, m = on.year, on.month
        while len(out) < n:
            letter = self.code_by_month[m]
            if letter in spec.expiry_month_codes:
                info = self.parse(self.code_for(symbol, y, m))
                if info.last_trading_day >= on:
                    out.append(info)
            m += 1
            if m > 12:
                m, y = 1, y + 1
        return out

    def suggest_front(self, symbol: Symbol | str, on: date, policy: RollPolicy | None = None) -> ContractSuggestion:
        policy = policy or RollPolicy()
        listed = self.listed_after(symbol, on, n=3)
        front = listed[0]
        # data de rolagem: N pregões antes do último dia de negociação
        roll = front.last_trading_day
        for _ in range(policy.days_before):
            roll = self.cal.prev_trading_day(roll)
        if on >= roll:
            chosen = listed[1]
            reason = (f"{front.code} tem último pregão em {front.last_trading_day:%d/%m/%Y}; pela política "
                      f"(rolar {policy.days_before} pregões antes), a rolagem ocorreu em {roll:%d/%m/%Y}. "
                      f"Sugerido {chosen.code}.")
        else:
            chosen = front
            reason = (f"{front.code} é o vencimento mais próximo (último pregão {front.last_trading_day:%d/%m/%Y}); "
                      f"rolagem prevista para {roll:%d/%m/%Y}.")
        if not chosen.calendar_coverage:
            reason += " ATENÇÃO: ano sem cobertura no arquivo de feriados; datas podem estar incorretas."
        return ContractSuggestion(chosen, reason, policy, [c.code for c in listed if c.code != chosen.code])
