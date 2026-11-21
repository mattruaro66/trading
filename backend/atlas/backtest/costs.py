"""Modelo de custos operacionais (configurável).

Os valores padrão de corretagem/emolumentos são ZERO de propósito: o ATLAS não
inventa tarifas. Configure com a tabela da sua corretora e a tabela vigente da
B3. Relatórios exibem um aviso enquanto os custos não estiverem configurados.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class CostModel(BaseModel):
    contracts: int = Field(1, ge=1, le=1000, description="Quantidade hipotética de minicontratos")
    brokerage_per_contract_side: float = Field(0.0, ge=0, description="Corretagem R$ por contrato por lado")
    exchange_fees_per_contract_side: float = Field(0.0, ge=0, description="Emolumentos + taxas B3 R$ por contrato por lado")
    other_fixed_per_trade: float = Field(0.0, ge=0, description="Outros custos fixos R$ por operação (ida+volta)")
    entry_slippage_ticks: float = Field(1.0, ge=0)
    exit_slippage_ticks: float = Field(1.0, ge=0, description="Aplicado a stops, saídas por tempo/sessão/lacuna")
    target_slippage_ticks: float = Field(0.0, ge=0, description="Aplicado a alvos (ordens limitadas)")
    spread_ticks: float = Field(0.0, ge=0, description="Meio spread adicional por lado, em ticks (0 = não modelado)")

    @property
    def fees_configured(self) -> bool:
        return (self.brokerage_per_contract_side + self.exchange_fees_per_contract_side + self.other_fixed_per_trade) > 0

    def round_trip_fees(self) -> float:
        per_side = self.brokerage_per_contract_side + self.exchange_fees_per_contract_side
        return round(per_side * 2 * self.contracts + self.other_fixed_per_trade, 2)

    def warnings(self) -> list[str]:
        w = []
        if not self.fees_configured:
            w.append("Corretagem/emolumentos não configurados (R$ 0,00): resultado líquido considera apenas slippage.")
        if self.spread_ticks == 0:
            w.append("Spread não modelado explicitamente (apenas slippage fixo em ticks).")
        return w
