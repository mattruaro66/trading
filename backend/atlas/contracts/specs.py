"""Especificações versionadas de WIN e WDO e cálculo financeiro.

Nunca use multiplicadores genéricos: todo cálculo financeiro passa por
``ContractSpec``. Os valores vêm de ``data/reference/contract_specs.json``.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path

from atlas.core.types import Symbol

REFERENCE_DIR = Path(__file__).resolve().parents[2] / "data" / "reference"


@dataclass(frozen=True)
class ContractSpec:
    symbol: Symbol
    name: str
    version: str
    effective_from: date
    tick_size: float
    point_value_brl: float
    price_decimals: int
    expiry_month_codes: tuple[str, ...]
    expiry_rule: str
    source: str
    validated: bool
    kind: str = "future"  # future | fx
    contract_size: float | None = None
    quote_currency: str = "BRL"
    account_currency: str = "BRL"
    lot_step: float = 1.0
    min_lot: float = 1.0

    @property
    def tick_value_brl(self) -> float:
        return round(self.tick_size * self.point_value_brl, 10)

    # ---- preço e ticks -------------------------------------------------
    def _d(self, x: float) -> Decimal:
        return Decimal(repr(float(x)))  # float() normaliza escalares numpy

    def round_to_tick(self, price: float, mode: str = "nearest") -> float:
        """Arredonda ao tick. mode: nearest | down | up."""
        tick = self._d(self.tick_size)
        q = self._d(price) / tick
        rounding = {"nearest": ROUND_HALF_UP, "down": ROUND_FLOOR, "up": ROUND_CEILING}[mode]
        n = q.to_integral_value(rounding=rounding)
        return float(n * tick)

    def is_on_tick(self, price: float, tol: float = 1e-9) -> bool:
        n = price / self.tick_size
        return math.isclose(n, round(n), abs_tol=tol)

    def ticks(self, n: int | float) -> float:
        return float(self._d(self.tick_size) * Decimal(str(n)))

    def price_diff_to_ticks(self, diff: float) -> float:
        return float(self._d(diff) / self._d(self.tick_size))

    # ---- financeiro -----------------------------------------------------
    def value_per_point(self, qty: float = 1.0, price: float | None = None) -> float:
        """Valor, na moeda da conta, de 1 ponto de preço para ``qty`` contratos/lotes."""
        if self.kind == "fx":
            v = self.contract_size * qty
            if self.quote_currency != self.account_currency:  # ex.: USDJPY → converte ienes para dólares
                if not price:
                    raise ValueError("Preço necessário para converter o resultado para a moeda da conta")
                v = v / price
            return v
        return self.point_value_brl * qty

    def pnl_money(self, points: float, qty: float = 1.0, price: float | None = None) -> float:
        """Resultado na moeda da conta (BRL para WIN/WDO, USD para os pares de forex)."""
        if self.kind != "fx":
            return self.pnl_brl(points, qty)
        return round(points * self.value_per_point(qty, price), 2)

    def size_for_risk(self, risk_points: float, budget: float, price: float | None = None) -> float:
        """Maior quantidade (múltiplo do passo de lote) cuja perda no stop não passa de ``budget``. 0 se nem o mínimo cabe."""
        per_unit = risk_points * self.value_per_point(1.0, price)
        if per_unit <= 0:
            return 0.0
        steps = int((budget / per_unit) / self.lot_step + 1e-9)
        qty = round(steps * self.lot_step, 8)
        return qty if qty >= self.min_lot else 0.0

    def pnl_brl(self, points: float, contracts: int = 1) -> float:
        """Resultado financeiro de uma variação em pontos para ``contracts`` minicontratos."""
        value = self._d(points) * self._d(self.point_value_brl) * Decimal(contracts)
        return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


@lru_cache(maxsize=4)
def load_specs(path: str | None = None) -> tuple[ContractSpec, ...]:
    p = Path(path) if path else REFERENCE_DIR / "contract_specs.json"
    raw = json.loads(p.read_text(encoding="utf-8"))
    validated = bool(raw.get("validated", False))
    out = []
    for s in raw["specs"]:
        out.append(
            ContractSpec(
                symbol=Symbol(s["symbol"]),
                name=s["name"],
                version=str(s["version"]),
                effective_from=_parse_date(s["effective_from"]),
                tick_size=float(s["tick_size"]),
                point_value_brl=float(s["point_value_brl"]),
                price_decimals=int(s["price_decimals"]),
                expiry_month_codes=tuple(s["expiry_month_codes"]),
                expiry_rule=s["expiry_rule"],
                source=s["source"],
                validated=validated,
                kind=s.get("kind", "future"),
                contract_size=s.get("contract_size"),
                quote_currency=s.get("quote_currency", "BRL"),
                account_currency=s.get("account_currency", "BRL"),
                lot_step=float(s.get("lot_step", 1)),
                min_lot=float(s.get("min_lot", 1)),
            )
        )
    return tuple(out)


@lru_cache(maxsize=4)
def month_codes(path: str | None = None) -> dict[str, int]:
    p = Path(path) if path else REFERENCE_DIR / "contract_specs.json"
    return dict(json.loads(p.read_text(encoding="utf-8"))["month_codes"])


def get_spec(symbol: Symbol | str, on: date | None = None) -> ContractSpec:
    """Retorna a versão da especificação vigente em ``on`` (padrão: a mais recente)."""
    sym = Symbol(symbol)
    candidates = [s for s in load_specs() if s.symbol == sym]
    if on is not None:
        candidates = [s for s in candidates if s.effective_from <= on]
    if not candidates:
        raise KeyError(f"Sem especificação para {sym} em {on}")
    return max(candidates, key=lambda s: s.effective_from)
