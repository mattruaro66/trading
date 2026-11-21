import { useEffect, useMemo, useRef, useState } from "react";
import CandleChart, { type ChartLevel, type ChartMarker } from "../components/CandleChart";
import { AlertDetail, CriteriaList, DatasetPicker, ErrorBox, Stat, SyntheticBanner, useDatasets } from "../components/Common";
import { type AlertDTO, type BarDTO, fmt, get, pct, post } from "../api";

interface Step {
  index: number;
  bar: BarDTO;
  regime: any;
  marks: { label: string; side: string }[];
  evaluations: { side: string; setup: string; stage: string; passed: boolean; criteria: any[] }[];
  signals: { side: string; setup: string; entry: number; stop: number; target: number }[];
  exec_events: { kind: string; ts: string; reason: string | null; trade: any; orders: SimOrder[] }[];
  position: Position;
  alert_events: { alert_id: string; state: string; state_label: string; reason: string }[];
  data_ok: boolean;
}

interface SimOrder {
  order_id: number; group: number; role: string; role_pt: string; action: string; action_pt: string; type_pt: string;
  qty: number; price: number | null; status: string; status_pt: string; created_ts: string; done_ts: string | null;
  fill_price: number | null; note: string; setup: string;
}
interface Position {
  state: string; realized_brl: number; closed_trades: number; qty?: number; entry_price?: number; stop_price?: number;
  target_price?: number; mark_price?: number; unrealized_points?: number; unrealized_brl?: number; bars_held?: number; setup?: string;
}

const STATE_PT: Record<string, string> = { flat: "Zerado", pending_entry: "Ordem de entrada pendente", long: "Comprado", short: "Vendido" };

const REGIME_PT: Record<string, string> = {
  BULL_TREND: "Tendência de alta", BEAR_TREND: "Tendência de baixa", TRADING_RANGE: "Trading range",
  TIGHT_TRADING_RANGE: "Trading range estreito", UNDEFINED: "Indefinido (aquecimento)",
};

export default function Replay() {
  const { datasets } = useDatasets();
  const [ds, setDs] = useState("");
  const [profile, setProfile] = useState("daytrade");
  const [session, setSession] = useState<any>(null);
  const [bars, setBars] = useState<BarDTO[]>([]);
  const [markers, setMarkers] = useState<ChartMarker[]>([]);
  const [last, setLast] = useState<Step | null>(null);
  const [levels, setLevels] = useState<ChartLevel[]>([]);
  const [log, setLog] = useState<string[]>([]);
  const [alerts, setAlerts] = useState<AlertDTO[]>([]);
  const [trades, setTrades] = useState<any[]>([]);
  const [orders, setOrders] = useState<SimOrder[]>([]);
  const [selected, setSelected] = useState<AlertDTO | null>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(4);
  const [error, setError] = useState<string | null>(null);
  const busy = useRef(false);

  const current = useMemo(() => datasets.find((d) => `${d.symbol}|${d.contract}` === ds), [datasets, ds]);

  async function create() {
    if (!current) return;
    setError(null);
    setPlaying(false);
    try {
      const s = await post<any>("/api/replay/sessions", { symbol: current.symbol, contract: current.contract, params: { profile } });
      setSession(s);
      setBars([]);
      setMarkers([]);
      setLast(null);
      setLevels([]);
      setLog([]);
      setAlerts([]);
      setTrades([]);
      setOrders([]);
      setSelected(null);
    } catch (e: any) {
      setError(e.message);
    }
  }

  async function step(n: number) {
    if (!session || busy.current) return;
    busy.current = true;
    try {
      const r = await post<{ steps: Step[]; finished: boolean }>(`/api/replay/sessions/${session.session_id}/step`, { n });
      if (!r.steps.length) {
        setPlaying(false);
        return;
      }
      const newMarks: ChartMarker[] = [];
      const newLog: string[] = [];
      let lv: ChartLevel[] | null = null;
      for (const s of r.steps) {
        for (const m of s.marks) {
          newMarks.push({ ts_open: s.bar.ts_open, time: s.bar.time, text: m.label, position: m.side === "long" ? "belowBar" : "aboveBar", color: m.side === "long" ? "#4ade80" : "#f87171" });
        }
        for (const sig of s.signals) {
          newMarks.push({ ts_open: s.bar.ts_open, time: s.bar.time, text: `SINAL ${sig.setup}`, position: sig.side === "long" ? "belowBar" : "aboveBar", color: "#facc15", shape: sig.side === "long" ? "arrowUp" : "arrowDown" });
          lv = [
            { price: sig.entry, color: "#facc15", title: "entrada" },
            { price: sig.stop, color: "#f87171", title: "stop" },
            { price: sig.target, color: "#4ade80", title: "alvo" },
          ];
        }
        for (const e of s.exec_events) {
          if (e.kind === "closed") {
            lv = [];
            newLog.push(`${e.ts.slice(11, 16)} encerramento hipotético: ${e.reason} (${fmt(e.trade?.net_brl)} R$)`);
          } else if (e.kind === "order_expired" || e.kind === "order_cancelled") {
            lv = [];
          }
        }
        for (const a of s.alert_events) newLog.push(`${s.bar.ts_open.slice(11, 16)} [${a.state_label}] ${a.reason}`);
      }
      const upd = r.steps.flatMap((s) => s.exec_events.flatMap((e) => e.orders ?? []));
      if (upd.length) setOrders((prev) => {
        const m = new Map(prev.map((o) => [o.order_id, o]));
        upd.forEach((o) => m.set(o.order_id, o));
        return [...m.values()].sort((a, b) => b.order_id - a.order_id).slice(0, 300);
      });
      setBars((b) => [...b, ...r.steps.map((s) => s.bar)]);
      setMarkers((m) => [...m, ...newMarks]);
      setLast(r.steps[r.steps.length - 1]);
      if (lv !== null) setLevels(lv);
      if (newLog.length) setLog((l) => [...newLog.reverse(), ...l].slice(0, 300));
      if (r.finished) setPlaying(false);
    } catch (e: any) {
      setError(e.message);
      setPlaying(false);
    } finally {
      busy.current = false;
    }
  }

  async function refreshAlerts() {
    if (!session) return;
    const r = await get<any>(`/api/replay/sessions/${session.session_id}`);
    setAlerts((r.alerts as AlertDTO[]).slice().reverse());
    setTrades((r.closed_trades as any[]).slice().reverse());
  }

  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => step(1), Math.max(40, 1000 / speed));
    return () => clearInterval(id);
  }, [playing, speed, session]);

  useEffect(() => {
    if (log.length) refreshAlerts();
  }, [log.length]);

  const cand = last?.evaluations.filter((e) => e.stage === "candidate") ?? [];

  return (
    <div className="space-y-3">
      <div className="card flex flex-wrap items-end gap-3">
        <div className="flex flex-col"><span className="label">Conjunto de dados</span><DatasetPicker datasets={datasets} value={ds} onChange={setDs} /></div>
        <div className="flex flex-col"><span className="label">Perfil (5 min)</span>
          <select className="input" value={profile} onChange={(e) => setProfile(e.target.value)}>
            <option value="daytrade">Day trade (2R, 24 candles)</option>
            <option value="scalp">Scalp (1R, 6 candles)</option>
          </select>
        </div>
        <button className="btn" onClick={create} disabled={!current}>Nova sessão de replay</button>
        {session && (
          <>
            <button className="btn-ghost" onClick={() => setPlaying((p) => !p)}>{playing ? "⏸ Pausar" : "▶ Reproduzir"}</button>
            <button className="btn-ghost" onClick={() => step(1)} disabled={playing}>+1 candle</button>
            <button className="btn-ghost" onClick={() => step(12)} disabled={playing}>+1 hora</button>
            <button className="btn-ghost" onClick={() => step(111)} disabled={playing}>+1 sessão</button>
            <div className="flex flex-col"><span className="label">Velocidade: {speed} candles/s</span>
              <input type="range" min={1} max={25} value={speed} onChange={(e) => setSpeed(+e.target.value)} /></div>
            <span className="text-sm text-slate-400">{bars.length} / {session.total} candles revelados</span>
          </>
        )}
      </div>
      <ErrorBox error={error} />
      <SyntheticBanner show={!!current?.synthetic} />
      <div className="grid grid-cols-1 xl:grid-cols-4 gap-3">
        <div className="xl:col-span-3 card p-2">
          <CandleChart bars={bars} markers={markers} levels={levels} height={460} />
          <div className="text-xs text-slate-500 mt-1">
            O servidor revela um candle de 5 min por vez; nenhum candle futuro é enviado ao navegador. Marcações H1/H2/L1/L2 são
            registradas no candle em que a contagem ocorre.
          </div>
        </div>
        <div className="card space-y-2 text-sm">
          <div className="font-semibold">Contexto atual</div>
          {last ? (
            <>
              <div className="text-lg">{REGIME_PT[last.regime.regime] ?? last.regime.regime}</div>
              <div className="grid grid-cols-2 gap-2">
                <Stat label="Acima da EMA20" value={pct(last.regime.frac_above_ema)} />
                <Stat label="Inclinação (ATR)" value={fmt(last.regime.ema_slope_atr, 2)} />
                <Stat label="Eficiência" value={fmt(last.regime.efficiency, 2)} />
                <Stat label="Sobreposição" value={fmt(last.regime.overlap_mean, 2)} />
                <Stat label="Always In (aprox.)" value={last.regime.always_in} />
                <Stat label="Dados" value={last.data_ok ? "válidos" : "COM PROBLEMAS"} />
              </div>
              {cand.map((c) => (
                <div key={c.side} className="border-t border-slate-800 pt-2">
                  <div className={c.passed ? "text-yellow-300" : "text-slate-400"}>
                    Candidata {c.setup} ({c.side === "long" ? "compra" : "venda"}) — {c.passed ? "configuração confirmada" : "critérios pendentes"}
                  </div>
                  <CriteriaList criteria={c.criteria} />
                </div>
              ))}
            </>
          ) : <div className="text-slate-500">Inicie uma sessão e avance os candles.</div>}
        </div>
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-3">
        <div className="card">
          <div className="font-semibold mb-2">Registro de eventos</div>
          <div className="h-64 overflow-auto text-xs font-mono space-y-0.5">{log.map((l, i) => <div key={i}>{l}</div>)}</div>
        </div>
        <div className="card">
          <div className="font-semibold mb-2">Alertas simulados (configurações confirmadas)</div>
          <div className="h-64 overflow-auto">
            <table className="tbl"><tbody>
              {alerts.map((a) => (
                <tr key={a.alert_id} className="cursor-pointer hover:bg-slate-800" onClick={() => setSelected(a)}>
                  <td>{a.payload.bar_ts_close?.slice(5, 16).replace("T", " ")}</td>
                  <td>{a.payload.setup} {a.payload.side === "long" ? "▲" : "▼"}</td>
                  <td>{a.state_label}</td>
                </tr>
              ))}
            </tbody></table>
          </div>
        </div>
        <div className="card">
          <div className="font-semibold mb-2">Operações hipotéticas</div>
          <div className="text-xs text-slate-400 mb-1">Mesmo simulador do backtest (política conservadora intrabar).</div>
          <div className="h-56 overflow-auto">
            <table className="tbl"><thead><tr><th>Entrada</th><th>Lado</th><th>Saída</th><th>R</th><th>R$ líq.</th></tr></thead><tbody>
              {trades.map((t) => (
                <tr key={t.trade_id}>
                  <td>{t.entry_ts.slice(5, 16).replace("T", " ")}</td>
                  <td>{t.side === "long" ? "▲" : "▼"}</td>
                  <td>{t.exit_reason}</td>
                  <td>{fmt(t.r_multiple, 2)}</td>
                  <td className={t.net_brl >= 0 ? "text-green-400" : "text-red-400"}>{fmt(t.net_brl)}</td>
                </tr>
              ))}
            </tbody></table>
          </div>
        </div>
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-4 gap-3">
        <div className="card text-sm space-y-2">
          <div className="font-semibold">Posição simulada</div>
          {last?.position ? (
            <div className="grid grid-cols-2 gap-2">
              <Stat label="Situação" value={STATE_PT[last.position.state] ?? last.position.state} />
              <Stat label="Resultado realizado" value={`R$ ${fmt(last.position.realized_brl)}`} hint={`${last.position.closed_trades} operações`} />
              {last.position.entry_price !== undefined && <>
                <Stat label="Entrada" value={fmt(last.position.entry_price)} hint={`${last.position.qty} contrato(s) · ${last.position.setup}`} />
                <Stat label="Último preço" value={fmt(last.position.mark_price)} />
                <Stat label="Stop / alvo" value={`${fmt(last.position.stop_price)} / ${fmt(last.position.target_price)}`} />
                <Stat label="Resultado aberto" value={`R$ ${fmt(last.position.unrealized_brl)}`} hint={`${fmt(last.position.unrealized_points)} pts · ${last.position.bars_held} candles`} />
              </>}
            </div>
          ) : <div className="text-slate-500">—</div>}
          <div className="text-xs text-amber-300">Execução simulada pelo método da Estratégia A. Nenhuma ordem é enviada a corretora ou bolsa.</div>
        </div>
        <div className="card xl:col-span-3">
          <div className="font-semibold mb-1">Livro de ordens simulado</div>
          <div className="text-xs text-slate-400 mb-1">Entrada stop válida por 1 candle → ao executar, stop e alvo entram como OCO → a execução de um cancela o outro; saídas por tempo/sessão são a mercado.</div>
          <div className="max-h-64 overflow-auto">
            <table className="tbl">
              <thead><tr><th>#</th><th>Grupo</th><th>Criada</th><th>Função</th><th>Lado</th><th>Tipo</th><th>Preço</th><th>Situação</th><th>Execução</th><th>Obs.</th></tr></thead>
              <tbody>{orders.map((o) => (
                <tr key={o.order_id}>
                  <td>{o.order_id}</td><td>{o.group} {o.setup}</td><td>{o.created_ts.slice(5, 16).replace("T", " ")}</td>
                  <td>{o.role_pt}</td><td className={o.action === "buy" ? "text-green-400" : "text-red-400"}>{o.action_pt} {o.qty}</td>
                  <td>{o.type_pt}</td><td>{o.price === null ? "—" : fmt(o.price)}</td>
                  <td className={o.status === "filled" ? "text-sky-300" : o.status === "working" ? "text-yellow-300" : "text-slate-500"}>{o.status_pt}</td>
                  <td>{o.fill_price === null ? "" : `${fmt(o.fill_price)} · ${o.done_ts?.slice(11, 16)}`}</td><td className="text-xs text-slate-400">{o.note}</td>
                </tr>))}</tbody>
            </table>
          </div>
        </div>
      </div>
      {selected && (
        <div className="card">
          <div className="flex justify-between mb-2"><div className="font-semibold">Painel analítico do alerta</div>
            <button className="btn-ghost" onClick={() => setSelected(null)}>Fechar</button></div>
          <AlertDetail alert={selected} />
        </div>
      )}
    </div>
  );
}
