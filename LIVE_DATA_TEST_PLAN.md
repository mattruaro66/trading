# Plano de Testes — Live Market Data

Todos os eventos usados nos testes são **sintéticos** (`synthetic=True`, gerados por `atlas/live/synthetic.py` ou escritos à mão nos testes).

| # | Requisito | Teste(s) | Status |
|---|---|---|---|
| 1 | Conexão e desconexão | `test_live_monitor.py::test_connect_disconnect_and_simulated_flag` | ✅ |
| 2 | Falha de autenticação | `test_auth_failure` | ✅ |
| 3 | Reconexão | `test_reconnect_with_recovery_rebuilds_and_resumes_alerts`, `test_reconnect_exhaustion_marks_failed`, `test_backoff_policy_exponential_with_jitter` | ✅ |
| 4 | Eventos duplicados | `test_live_candle_engine.py::test_duplicates_by_trade_id_and_sequence`, `test_no_identity_trades_are_not_dropped` | ✅ |
| 5 | Eventos fora de ordem | `test_out_of_order_within_candle_sets_correct_open_close` | ✅ |
| 6 | Candles de 5 min | `test_builds_5min_ohlcv_and_closes_after_grace`, `test_advance_clock_closes_idle_candle` | ✅ |
| 7 | Volume | `test_builds_5min_ohlcv...`, `test_volume_unavailable_kept_null` | ✅ |
| 8 | Horário da sessão | `test_session_hours_enforced` | ✅ |
| 9 | Troca de vencimento | `test_other_contract_rejected` (engine por contrato), `test_price_action.py::test_engine_resets_on_gap_and_contract_change` | ✅ (sem rolagem automática ao vivo — seleção explícita) |
| 10 | Dados atrasados | `test_late_event_invalidates_closed_candle` | ✅ |
| 11 | Lacunas | `test_sequence_gap_flags_candle_suspect`, `test_empty_bucket_reports_gap_without_fake_candle` | ✅ |
| 12 | Persistência | `test_live_candles_and_events_persisted` | ✅ |
| 13 | Publicação via WebSocket | `test_ws_publication` | ✅ |
| 14 | Integração com Price Action | `test_candles_published_and_fed_to_price_action` | ✅ |
| 15 | Suspensão de alertas | `test_alerts_suspended_during_outage`, `test_heartbeat_timeout_degrades` | ✅ |
| 16 | Recuperação após interrupção | `test_rebuild_from_recovered_events`, `test_reconnect_with_recovery...` (candles reconstruídos idênticos ao fluxo completo) | ✅ |
| — | Provedor CSV não simula conexão | `test_csv_provider_is_historical_only` | ✅ |
| — | Cedro não finge conexão | `test_cedro_pending_never_fakes_connection` | ✅ |
| — | **Conexão real com fornecedor** | — | ❌ Bloqueado (documentação/credenciais) |
| — | Teste prolongado (sessão completa com feed real) | — | ❌ Bloqueado |

Executar:
```bash
cd backend && pytest -q tests/test_live_candle_engine.py tests/test_live_monitor.py
```
