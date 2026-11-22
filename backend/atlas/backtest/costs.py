"""Modelo de custos operacionais (configurável).

Os valores padrão de corretagem/emolumentos são ZERO de propósito: o ATLAS não
inventa tarifas. Configure com a tabela da sua corretora e a tabela vigente da
B3. Relatórios exibem um aviso enquanto os custos não estiverem configurados.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class CostModel(BaseModel):
    contracts: float = Field(1, gt=0, le=1000, description="Quantidade fixa de contratos/lotes por operação")
    risk_per_trade: float | None = Field(None, gt=0, description="Risco fixo por operação na moeda da conta; se definido, "
                                         "a quantidade é calculada para que a perda no stop não passe deste valor")
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

    def round_trip_fees(self, qty: float | None = None) -> float:
        per_side = self.brokerage_per_contract_side + self.exchange_fees_per_contract_side
        q = self.contracts if qty is None else qty
        return round(per_side * 2 * q + self.other_fixed_per_trade, 2)

    def quantity(self, spec, risk_points: float, price: float | None, n_lots: int = 1) -> float:
        """Quantidade por lote: fixa (``contracts``) ou dimensionada pelo risco (orçamento dividido entre os lotes).

        No dimensionamento, a perda no stop inclui slippage de entrada e saída e o spread (ida e volta), para que o
        orçamento seja um limite de perda real (gaps além do stop podem ultrapassá-lo).
        """
        if self.risk_per_trade is None:
            return self.contracts
        # forex: + 2 ticks pelo arredondamento do stop e do preço de saída (stops em pontos fracionários)
        rounding = 2 if getattr(spec, "kind", "future") == "fx" else 0
        cost_pts = spec.ticks(self.entry_slippage_ticks + self.exit_slippage_ticks + 2 * self.spread_ticks + rounding)
        per_lot_budget = self.risk_per_trade / n_lots
        qty = spec.size_for_risk(risk_points + cost_pts, per_lot_budget, price)
        fees_per_unit = (self.brokerage_per_contract_side + self.exchange_fees_per_contract_side) * 2
        while qty > 0 and spec.pnl_money(risk_points + cost_pts, qty, price) + fees_per_unit * qty > per_lot_budget + 1e-9:
            qty = round(qty - spec.lot_step, 8)
            if qty < spec.min_lot:
                return 0.0
        return qty

    def warnings(self) -> list[str]:
        w = []
        if not self.fees_configured:
            w.append("Corretagem/emolumentos não configurados (R$ 0,00): resultado líquido considera apenas slippage.")
        if self.spread_ticks == 0:
            w.append("Spread não modelado explicitamente (apenas slippage fixo em ticks).")
        return w
