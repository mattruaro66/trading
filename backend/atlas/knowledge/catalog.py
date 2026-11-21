"""Trading Knowledge Engine (MVP): catálogo estático de conceitos.

Cada conceito separa explicitamente (§7.4):
1. ``author_concept`` — resumo, em palavras do ATLAS, de uma ideia descrita pelo autor (NÃO é citação literal);
2. ``atlas_interpretation`` — como o ATLAS traduz a ideia;
3. ``quant_rule`` — a regra implementada (aponta para STRATEGIES.md);
4. ``observed_results`` — sempre vazio aqui; resultados vêm de backtests.

Nenhuma citação literal ou número de página é fornecido, pois não foram
verificados contra o texto original. A Fase 2 adicionará RAG sobre materiais
importados legalmente, com referência rastreável.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Reference:
    ref_id: str
    author: str
    title: str
    edition: str
    source: str
    location: str | None
    usage_permission: str
    added_on: date


@dataclass(frozen=True)
class Concept:
    concept_id: str
    school: str
    name: str
    author_concept: str
    atlas_interpretation: str
    quant_rule: str
    references: tuple[str, ...]
    implemented: bool
    limitations: tuple[str, ...] = field(default_factory=tuple)


REFERENCES = {
    "btc_glossary": Reference("btc_glossary", "Al Brooks / Brooks Trading Course", "Price Action Trading Glossary", "online",
                              "https://www.brookstradingcourse.com/price-action-trading-terms-glossary/", None,
                              "Consulta pública; definições resumidas (sem reprodução integral)", date(2026, 11, 21)),
    "brooks_trends": Reference("brooks_trends", "Al Brooks", "Trading Price Action Trends", "1ª ed., Wiley, 2012",
                               "Livro (não incluído no repositório)", None,
                               "Referência bibliográfica apenas; nenhum trecho reproduzido", date(2026, 11, 21)),
    "brooks_ranges": Reference("brooks_ranges", "Al Brooks", "Trading Price Action Trading Ranges", "1ª ed., Wiley, 2012",
                               "Livro (não incluído no repositório)", None,
                               "Referência bibliográfica apenas; nenhum trecho reproduzido", date(2026, 11, 21)),
    "brooks_reversals": Reference("brooks_reversals", "Al Brooks", "Trading Price Action Reversals", "1ª ed., Wiley, 2012",
                                  "Livro (não incluído no repositório)", None,
                                  "Referência bibliográfica apenas; nenhum trecho reproduzido", date(2026, 11, 21)),
    "brooks_bar_by_bar": Reference("brooks_bar_by_bar", "Al Brooks", "Reading Price Charts Bar by Bar", "1ª ed., Wiley, 2009",
                                   "Livro (não incluído no repositório)", None,
                                   "Referência bibliográfica apenas; nenhum trecho reproduzido", date(2026, 11, 21)),
}

CONCEPTS = [
    Concept("trend_bar", "brooks", "Trend bar (barra de tendência)",
            "Barra com corpo relevante em relação à amplitude, indicando controle de um dos lados naquele intervalo.",
            "Classificamos pelo tamanho relativo do corpo e pela direção do fechamento.",
            "STRATEGIES.md §2: body_ratio ≥ 0,50 e C>O (alta) ou C<O (baixa).",
            ("brooks_bar_by_bar", "brooks_trends"), True),
    Concept("doji", "brooks", "Doji",
            "Barra de corpo pequeno, que reflete equilíbrio momentâneo entre compradores e vendedores.",
            "Corpo pequeno relativo à amplitude.", "STRATEGIES.md §2: body_ratio ≤ 0,30 ou amplitude nula.",
            ("brooks_bar_by_bar",), True),
    Concept("signal_bar", "brooks", "Signal bar (barra de sinal)",
            "Barra que antecede a entrada e cuja extremidade define o preço da ordem stop; sua qualidade importa.",
            "Qualidade avaliada por posição do fechamento, direção do corpo e amplitude relativa.",
            "STRATEGIES.md §5 critérios C3–C5.", ("brooks_bar_by_bar", "brooks_trends"), True),
    Concept("trend_vs_range", "brooks", "Tendência vs. trading range",
            "Tendências mostram progresso direcional e barras majoritariamente de um lado da média; ranges mostram sobreposição e pouco progresso.",
            "Medimos fração de fechamentos acima da EMA20, inclinação da EMA em ATRs, eficiência direcional e sobreposição média.",
            "STRATEGIES.md §3.", ("brooks_trends", "brooks_ranges"), True,
            ("Não distingue spike/canal, canais largos/estreitos ou reversões maiores.",)),
    Concept("high1_high2", "brooks", "High 1 / High 2 e Low 1 / Low 2",
            "Contagem das tentativas de retomada dentro de um pullback: a primeira barra que supera a máxima anterior é H1; após nova pausa/queda, a próxima é H2 (espelho para L1/L2).",
            "Máquina de estados que conta essas tentativas sem olhar candles futuros.",
            "STRATEGIES.md §4.", ("btc_glossary", "brooks_trends", "brooks_bar_by_bar"), True,
            ("Brooks aplica a contagem com julgamento contextual; a versão algorítmica é uma aproximação.",)),
    Concept("tight_trading_range", "brooks", "Tight trading range / barbwire",
            "Barras muito sobrepostas, com reversões pequenas demais para operar com ordens stop; iniciantes devem esperar um rompimento claro.",
            "3+ barras com fechamento dentro da barra anterior, numa faixa de até 25% do range diário médio; contagem H/L suspensa até rompimento com follow-through.",
            "BROOKS_RULES.md regras 4–6; atlas/price_action/lateral.py.", ("btc_glossary",), True,
            ("O limite de altura vem de um exemplo do livro (≈3 pontos para range diário de 10–15 no Emini).",)),
    Concept("breakout_follow_through", "brooks", "Rompimento e follow-through",
            "A barra de rompimento costuma ser uma trend bar forte; a barra seguinte (follow-through) deve estender o movimento, com corpo na mesma direção.",
            "Rompimento = fechamento fora da faixa com corpo a favor; confirmação = barra seguinte com corpo a favor fechando fora da faixa.",
            "BROOKS_RULES.md regras 5–6.", ("btc_glossary",), True),
    Concept("traders_equation", "brooks", "Equação do trader",
            "Probabilidade × recompensa deve superar (1 − probabilidade) × risco; sem certeza, assumir 40–50% e buscar recompensa ≥ 2× o risco.",
            "Relatórios mostram a taxa de acerto de empate de cada plano.", "BROOKS_RULES.md regra 10.", ("btc_glossary",), True),
    Concept("second_entry", "brooks", "Segunda entrada (second entry)",
            "Em tendência, a segunda tentativa de retomar a direção após um pullback costuma ser mais confiável que a primeira.",
            "Exigimos regime de tendência, barra candidata ao H2/L2 e filtros de qualidade.",
            "STRATEGIES.md §5.", ("brooks_trends",), True),
    Concept("always_in", "brooks", "Always In",
            "Ideia de que, a qualquer momento, um trader teria de estar comprado ou vendido; a direção 'sempre posicionada' resume o viés do mercado.",
            "Aproximação: posição do fechamento em relação à EMA20 e sinal da inclinação.",
            "STRATEGIES.md §3 (always_in).", ("brooks_trends",), True,
            ("Aproximação simplificada; não reproduz a leitura discricionária.",)),
    Concept("wedge", "brooks", "Wedge (cunha)", "Padrão de três impulsos que pode sinalizar exaustão.",
            "Não implementado.", "—", ("brooks_reversals",), False),
    Concept("double_top_bottom", "brooks", "Double top / double bottom", "Teste duplo de uma extremidade.",
            "Não implementado.", "—", ("brooks_reversals",), False),
    Concept("major_trend_reversal", "brooks", "Major Trend Reversal", "Reversão de tendência após rompimento da linha de tendência e teste do extremo.",
            "Não implementado.", "—", ("brooks_reversals",), False),
]


def get_concept(cid: str) -> Concept | None:
    return next((c for c in CONCEPTS if c.concept_id == cid), None)
