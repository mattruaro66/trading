import { useEffect, useRef, useState } from "react";
import CandleChart, { type ChartMarker } from "../components/CandleChart";
import { AlertDetail, DatasetPicker, ErrorBox, Stat, SyntheticBanner, useDatasets } from "../components/Common";
import { type AlertDTO, type BarDTO, fmt, get, post, wsUrl } from "../api";

// ---------------------------------------------------------------- Painel principal
export function Dashboard() {
  const { datasets } = useDatasets();
  const [info, setInfo] = useState<any>(null);
  const [contracts, setContracts] = useState<Record<string, any>>({});
  const [ds, setDs] = useState("");
  const [bars, setBars] = useState<BarDTO[]>([]);
  const [alerts, setAlerts] = useState<AlertDTO[]>([]);
  const [status, setStatus] = useState<any>(null);

  useEffect(() => {
    get<any>("/api/system/info").then(setInfo).catch(() => {});
    for (const s of ["WIN", "WDO"]) get<any>(`/api/market/contracts?symbol=${s}`).then((c) => setContracts((x) => ({ ...x, [s]: c }))).catch(() => {});
    get<any>("/api/alerts?limit=20").then((r) => setAlerts(r.alerts)).catch(() => {});
    get<any>("/api/market/status").then(setStatus).catch(() => {});
  }, []);
  useEffect(() => {
    if (!ds) return;
    const [symbol, contract] = ds.split("|");
    get<any>(`/api/market/candles?symbol=${symbol}&contract=${contract}&limit=600`).then((r) => setBars(r.bars));
  }, [ds]);
  const cur = datasets.find((d) => `${d.symbol}|${d.contract}` === ds);
  const ref = info?.reference_validation;

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-3">
        {["WIN", "WDO"].map((s) => (
          <div key={s} className="card text-sm space-y-1">
            <div className="font-semibold">{s} — {contracts[s]?.spec.name}</div>
            {contracts[s] && <>
              <div>Tick {contracts[s].spec.tick_size} · R$ {fmt(contracts[s].spec.point_value_brl)}/ponto · R$ {fmt(contracts[s].spec.tick_value_brl)}/tick</div>
              <div>Sugestão de contrato: <b>{contracts[s].suggestion.code}</b></div>
              <div className="text-xs text-slate-400">{contracts[s].suggestion.reason}</div>
              {!contracts[s].spec.validated && <div className="text-xs text-amber-300">Especificação ainda não validada contra a B3.</div>}
            </>}
          </div>
        ))}
        <div className="card text-sm space-y-1">
          <div className="font-semibold">Conexões</div>
          <div>Histórico (CSV/Parquet): disponível</div>
          <div>Replay de negócios: disponível (simulação)</div>
          <div>Cedro (ao vivo): <span className="text-amber-300">pendente — documentação/credenciais</span></div>
          {status?.live && <div>Monitor: {status.live.monitor_state} · {status.live.contract}</div>}
        </div>
        <div className="card text-sm space-y-1">
          <div className="font-semibold">Dados de referência</div>
          {ref && <>
            <div>Especificações: {ref.contract_specs ? "validadas" : <span className="text-amber-300">a validar</span>}</div>
            <div>Feriados: {ref.holidays ? "validados" : <span className="text-amber-300">a validar</span>} ({ref.holiday_years.join(", ")})</div>
            <div>Horários de sessão: {ref.sessions ? "validados" : <span className="text-amber-300">a validar</span>}</div>
          </>}
          <div className="text-xs text-slate-500">Calendário econômico: Fase 4 (não integrado).</div>
        </div>
      </div>
      <div className="card space-y-2">
        <div className="flex items-center gap-3"><div className="font-semibold">Gráfico 5 min</div><DatasetPicker datasets={datasets} value={ds} onChange={setDs} /></div>
        <SyntheticBanner show={!!cur?.synthetic} />
        <CandleChart bars={bars} height={420} follow={false} />
      </div>
      <div className="card">
        <div className="font-semibold mb-1">Últimos alertas registrados</div>
        <table className="tbl"><tbody>{alerts.map((a) => (
          <tr key={a.alert_id}><td>{a.created_ts.slice(0, 16).replace("T", " ")}</td><td>{a.payload.symbol} {a.payload.contract}</td>
            <td>{a.payload.setup ?? a.payload.kind}</td><td>{a.state}</td><td>{a.origin}</td><td>{a.synthetic ? "SINTÉTICO" : ""}</td></tr>
        ))}</tbody></table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- Alertas
export function Alerts() {
  const [alerts, setAlerts] = useState<AlertDTO[]>([]);
  const [sel, setSel] = useState<AlertDTO | null>(null);
  const [onlyConfirmed, setOnlyConfirmed] = useState(true);
  useEffect(() => { get<any>("/api/alerts?limit=500").then((r) => setAlerts(r.alerts)); }, []);
  const list = alerts.filter((a) => !onlyConfirmed || a.payload.kind === "confirmed_setup");
  return (
    <div className="grid grid-cols-1 xl:grid-cols-3 gap-3">
      <div className="card">
        <label className="text-sm flex gap-2 mb-2"><input type="checkbox" checked={onlyConfirmed} onChange={(e) => setOnlyConfirmed(e.target.checked)} />Somente configurações confirmadas</label>
        <div className="max-h-[70vh] overflow-auto">
          <table className="tbl"><tbody>{list.map((a) => (
            <tr key={a.alert_id} className="cursor-pointer hover:bg-slate-800" onClick={() => setSel(a)}>
              <td>{(a.payload.bar_ts_close ?? a.created_ts).slice(0, 16).replace("T", " ")}</td><td>{a.payload.symbol}</td>
              <td>{a.payload.setup} {a.payload.side === "long" ? "▲" : "▼"}</td><td>{a.state}</td><td className="text-xs">{a.origin}{a.synthetic ? " · SINT." : ""}</td>
            </tr>))}</tbody></table>
        </div>
      </div>
      <div className="card xl:col-span-2">{sel ? <AlertDetail alert={{ ...sel, events: sel.events.map((e: any) => ({ ...e, alert_id: sel.alert_id })) }} /> : <div className="text-slate-500">Selecione um alerta.</div>}</div>
    </div>
  );
}

// ---------------------------------------------------------------- Ao vivo
export function Live() {
  const [bars, setBars] = useState<BarDTO[]>([]);
  const [forming, setForming] = useState<BarDTO | null>(null);
  const [markers, setMarkers] = useState<ChartMarker[]>([]);
  const [status, setStatus] = useState<any>(null);
  const [events, setEvents] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [speed, setSpeed] = useState(60);
  const ws = useRef<WebSocket | null>(null);

  useEffect(() => {
    const s = new WebSocket(wsUrl("/ws/market"));
    ws.current = s;
    s.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.status) setStatus(m.status);
      if (m.type === "CandleUpdated") setForming(m.bar);
      else if (m.type === "CandleClosed") {
        setBars((b) => (b.length && b[b.length - 1].time >= m.bar.time ? b : [...b, m.bar]));
        setForming(null);
        for (const mk of m.analysis?.marks ?? []) {
          setMarkers((x) => [...x, { ts_open: m.bar.ts_open, time: m.bar.time, text: mk.label, position: mk.side === "long" ? "belowBar" : "aboveBar", color: mk.side === "long" ? "#4ade80" : "#f87171" }]);
        }
        for (const a of m.alert_events ?? []) setEvents((e) => [`${m.bar.ts_open.slice(11, 16)} [${a.state_label}] ${a.reason}`, ...e].slice(0, 200));
      } else if (m.type !== "hello") {
        setEvents((e) => [`${(m.ts ?? m.start ?? "").slice(11, 19)} ${m.type} ${m.reason ?? ""} ${m.note ?? ""}`, ...e].slice(0, 200));
      }
    };
    s.onerror = () => setError("WebSocket indisponível — o backend está rodando?");
    return () => s.close();
  }, []);

  async function start() {
    setError(null); setBars([]); setMarkers([]); setForming(null); setEvents([]);
    try {
      const r = await post<any>("/api/live/start", { provider: "replay", symbol: "WIN", contract: "WINJ26", synthetic_day: "2026-03-02", synthetic_buckets: 100, speed });
      setStatus(r.status);
    } catch (e: any) { setError(e.message); }
  }
  async function stop() { const r = await post<any>("/api/live/stop"); setStatus(r.status); }
  async function cedro() {
    try { await post("/api/live/start", { provider: "cedro" }); } catch (e: any) { setError(e.message); }
  }

  return (
    <div className="space-y-3">
      <div className="card flex flex-wrap gap-3 items-end">
        <button className="btn" onClick={start}>Iniciar replay de negócios SINTÉTICOS</button>
        <div className="flex flex-col"><span className="label">Velocidade (x tempo real)</span><input type="number" className="input w-24" value={speed} onChange={(e) => setSpeed(+e.target.value)} /></div>
        <button className="btn-ghost" onClick={stop}>Parar</button>
        <button className="btn-ghost" onClick={cedro}>Conectar Cedro</button>
      </div>
      <ErrorBox error={error} />
      {status?.simulated && <SyntheticBanner show />}
      <div className="grid grid-cols-1 xl:grid-cols-4 gap-3">
        <div className="card p-2 xl:col-span-3"><CandleChart bars={bars} forming={forming} markers={markers} height={440} /></div>
        <div className="card text-sm space-y-2">
          <div className="font-semibold">Estado da conexão</div>
          {status ? <div className="grid grid-cols-2 gap-2">
            <Stat label="Provedor" value={`${status.provider}${status.simulated ? " (simulação)" : ""}`} />
            <Stat label="Contrato" value={status.contract ?? "-"} />
            <Stat label="Estado" value={status.monitor_state ?? status.state} />
            <Stat label="Alertas" value={status.alerts_enabled ? "habilitados" : `suspensos (${status.alerts_block_reason ?? ""})`} />
            <Stat label="Último evento (bolsa)" value={(status.last_exchange_ts ?? "-").slice(11, 19)} />
            <Stat label="Latência p50/p95 (ms)" value={`${fmt(status.latency_ms_p50, 0)} / ${fmt(status.latency_ms_p95, 0)}`} />
            <Stat label="Reconexões" value={String(status.reconnect_attempts ?? 0)} />
            <Stat label="Negócios aceitos" value={String(status.candle_engine?.accepted ?? 0)} />
            <Stat label="Duplicados" value={String(status.candle_engine?.duplicates ?? 0)} />
            <Stat label="Atrasados" value={String(status.candle_engine?.late_events ?? 0)} />
          </div> : <div className="text-slate-500">Sem monitor ativo.</div>}
          <div className="text-xs text-slate-500">{status?.message}</div>
        </div>
      </div>
      <div className="card"><div className="font-semibold mb-1">Eventos</div>
        <div className="h-56 overflow-auto font-mono text-xs">{events.map((e, i) => <div key={i}>{e}</div>)}</div></div>
    </div>
  );
}

// ---------------------------------------------------------------- Metodologia
export function Methodology() {
  const [k, setK] = useState<any>(null);
  const [strat, setStrat] = useState<any>(null);
  useEffect(() => {
    get<any>("/api/knowledge/concepts").then(setK);
    get<any>("/api/strategies").then((r) => setStrat(r.strategies[0]));
  }, []);
  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
      <div className="card space-y-3">
        <div className="font-semibold">Conceitos (Al Brooks) — separação conceito / interpretação / regra</div>
        <div className="text-xs text-amber-300">{k?.note}</div>
        {k?.concepts.map((c: any) => (
          <div key={c.concept_id} className="border-l-2 border-slate-700 pl-2 text-sm">
            <div className="font-medium">{c.name} {c.implemented ? "" : <span className="text-slate-500">(não implementado)</span>}</div>
            <div><span className="text-slate-500">1. Conceito (resumo): </span>{c.author_concept}</div>
            <div><span className="text-slate-500">2. Interpretação ATLAS: </span>{c.atlas_interpretation}</div>
            <div><span className="text-slate-500">3. Regra implementada: </span>{c.quant_rule}</div>
            <div className="text-slate-500">4. Resultado estatístico: somente via backtest com dados reais.</div>
            {c.limitations.length > 0 && <div className="text-xs text-amber-300">Limitações: {c.limitations.join(" ")}</div>}
          </div>
        ))}
      </div>
      <div className="card space-y-2 text-sm">
        <div className="font-semibold">Estratégia A — especificação declarativa</div>
        {strat && <pre className="text-xs whitespace-pre-wrap text-slate-300">{JSON.stringify(strat.spec, null, 2)}</pre>}
        <div className="font-semibold">Parâmetros padrão</div>
        {strat && <pre className="text-xs whitespace-pre-wrap text-slate-300">{JSON.stringify(strat.defaults, null, 2)}</pre>}
      </div>
    </div>
  );
}

