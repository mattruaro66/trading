# Integração com Provedores de Dados

## 1. Cedro Technologies — Market Data (prioritário para avaliação)

### Situação: **PENDENTE (Etapa C não iniciada)**

Em 21/11/2026 foi feita uma tentativa de consultar a documentação oficial a partir do ambiente de desenvolvimento:

| URL tentada | Resultado |
|---|---|
| `https://www.cedrotech.com/` | Inacessível (domínio fora da rede permitida do ambiente) |
| `https://www.cedrotech.com/market-data` | Inacessível |
| `https://docs.cedrotech.com/` | Inacessível |
| `https://developers.cedrotech.com/` | Inacessível |
| `https://github.com/cedrotech` | Acessível; contém apenas um repositório `test` sem conteúdo relevante |

**Nada foi verificado** sobre protocolos, URLs, autenticação, comandos de assinatura, formato das mensagens, suporte a WIN/WDO, nomenclatura de vencimentos, histórico, custos, licenciamento ou recuperação. Por regra do projeto, **nenhum desses itens foi presumido** no código.

`CedroProvider` (`backend/atlas/live/providers.py`) existe apenas como ponto de extensão: todos os métodos lançam `ProviderNotConfigured` e o status é `not_configured`. A API responde `501` para `provider=cedro`.

### Checklist para concluir a integração
1. Obter a documentação oficial (contrato comercial/portal do desenvolvedor) e anexá-la ao repositório ou referenciá-la com versão/data.
2. Registrar neste arquivo, **com a fonte**:
   - endpoints e protocolos (WebSocket/socket/REST);
   - autenticação e renovação de credenciais;
   - formato real das mensagens de negócios e cotações (campos, tipos, fuso dos timestamps);
   - identificadores de negócio e sequência (necessários para duplicidade/lacunas);
   - suporte a WIN e WDO e como os vencimentos são identificados;
   - histórico intradiário e recuperação após queda;
   - limites de taxa, custos, licença de armazenamento e de análises derivadas.
3. Implementar `CedroProvider` mapeando mensagens → `MarketEvent` (campos indisponíveis = `None`).
4. Definir `supports_recovery` e `recover_events` somente se a recuperação for documentada.
5. Testes de unidade com mensagens de exemplo **da documentação** (marcadas como exemplos) + testes reais de conexão com credenciais autorizadas (fora do CI público).
6. Teste prolongado (sessão completa) comparando candles do conector com uma fonte de referência.

**Critério:** a etapa C só é concluída após teste real de conexão recebendo dados de WIN e WDO.

### Credenciais
Variáveis de ambiente do backend: `ATLAS_CEDRO_USERNAME`, `ATLAS_CEDRO_PASSWORD`, `ATLAS_CEDRO_ENDPOINT` (sem valor padrão). Nunca expostas ao frontend nem à API. Preenchê-las hoje **não** habilita conexão.

## 2. ReplayProvider (disponível)
Reproduz negócios em ordem de chegada com a mesma interface de um feed. Sempre `simulated=True`, rotulado como “SIMULAÇÃO” na API e na interface.
CSV aceito em `POST /api/live/start-file`:
```
exchange_timestamp,price[,quantity][,trade_id][,sequence_number][,received_timestamp]
```
Timestamps sem fuso são interpretados como America/Sao_Paulo.

## 3. CSVProvider (disponível)
Somente histórico (candles). `connect`/`subscribe` lançam `NotSupported` — não simula conectividade.

## 4. Como adicionar outro fornecedor
Implementar `MarketDataProvider`, registrar em `/api/market/providers` e em `/api/live/start`, escrever testes de normalização. Nenhuma outra camada precisa mudar.
