import { useEffect, useMemo, useState } from "react";
import { DatasetPicker, EquityChart, ErrorBox, Stat, StatsBlock, SyntheticBanner, useDatasets } from "../components/Common";
import { API_URL, ci, fmt, get, pct, post, postForm } from "../api";

export default function Lab() {
  const { datasets, reload } = useDatasets();
  const [runsKey, setRunsKey] = useState(0);
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <ImportCard onDone={reload} />
        <SyntheticCard onDone={reload} />
      </div>
      <BacktestCard datasets={datasets} onRun={() => setRunsKey((k) => k + 1)} />
      <RunsCard key={runsKey} />
    </div>
  );
}

function ImportCard({ onDone }: { onDone: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [symbol, setSymbol] = useState("WIN");
  const [contract, setContract] = useState("");
  const [label, setLabel] = useState("open");
  const [resample, setResample] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  async function submit() {
    if (!file) return;
    setError(null);
    const f = new FormData();
    f.append("file", file);
    f.append("symbol", symbol);
    f.append("contract", contract);
    f.append("timestamp_label", label);
    f.append("resample_from_1m", String(resample));
    try {
      setResult(await postForm("/api/data/import", f));
      onDone();
    } catch (e: any) {
      setError(e.message);
    }
  }
  return (
    <div className="card space-y-2">
      <div className="font-semibold">Importar histórico (CSV / Parquet)</div>
      <div className="text-xs text-slate-400">Formatos: canônico ATLAS, exportação MetaTrader 5, colunas em português. Somente 5 min (ou 1 min com reamostragem).</div>
      <div className="flex flex-wrap gap-2 items-end">
        <input type="file" accept=".csv,.txt,.parquet" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="text-sm" />
        <select className="input" value={symbol} onChange={(e) => setSymbol(e.target.value)}><option>WIN</option><option>WDO</option></select>
        <input className="input w-28" placeholder="ex.: WINZ26" value={contract} onChange={(e) => setContract(e.target.value.toUpperCase())} />
        <select className="input" value={label} onChange={(e) => setLabel(e.target.value)}>
          <option value="open">timestamp = abertura</option><option value="close">timestamp = fechamento</option>
        </select>
        <label className="text-sm flex items-center gap-1"><input type="checkbox" checked={resample} onChange={(e) => setResample(e.target.checked)} />1 min → 5 min</label>
        <button className="btn" onClick={submit} disabled={!file || !contract}>Importar</button>
      </div>
      <ErrorBox error={error} />
      {result && (
        <div className="text-sm space-y-1">
          <div>Válidos: <b>{result.summary.rows_valid}</b> / {result.summary.rows_in} · rejeitados {result.summary.rows_rejected} · suspeitos {result.summary.rows_suspect} · lacunas {result.summary.gaps} · inseridos {result.saved.inserted}</div>
          <ul className="list-disc ml-5 text-xs text-slate-400">{result.summary.assumptions.map((a: string) => <li key={a}>{a}</li>)}</ul>
          <div className="max-h-40 overflow-auto text-xs">
            {result.issues.slice(0, 100).map((i: any, k: number) => (
              <div key={k} className={i.severity === "error" ? "text-red-300" : "text-amber-300"}>[{i.kind}] {i.message} {i.ts ?? ""}</div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function SyntheticCard({ onDone }: { onDone: () => void }) {
  const [symbol, setSymbol] = useState("WIN");
  const [sessions, setSessions] = useState(40);
  const [msg, setMsg] = useState<string | null>(null);
  async function gen() {
    const contract = symbol === "WIN" ? "WINJ26" : "WDOJ26";
    const r = await post<any>("/api/data/synthetic", { symbol, contract, sessions, seed: 7 });
    setMsg(`Gerado ${r.contract} com ${r.saved.inserted} candles. ${r.warning}`);
    onDone();
  }
  return (
    <div className="card space-y-2">
      <div className="font-semibold">Dados sintéticos para teste do software</div>
      <div className="text-xs text-amber-300">Servem apenas para verificar o funcionamento do ATLAS. São armazenados com o sufixo _SINTETICO e marcados em toda a interface.</div>
      <div className="flex gap-2 items-end">
        <select className="input" value={symbol} onChange={(e) => setSymbol(e.target.value)}><option>WIN</option><option>WDO</option></select>
        <input type="number" className="input w-24" value={sessions} min={1} max={500} onChange={(e) => setSessions(+e.target.value)} />
        <span className="label">sessões</span>
        <button className="btn-ghost" onClick={gen}>Gerar</button>
      </div>
      {msg && <div className="text-sm text-slate-300">{msg}</div>}
    </div>
  );
}

function BacktestCard({ datasets, onRun }: { datasets: any[]; onRun: () => void }) {
  const [ds, setDs] = useState("");
  const [profile, setProfile] = useState("daytrade");
  const [strategy, setStrategy] = useState("h2l2_scale_momentum");
  const [stopMode, setStopMode] = useState("signal_bar");
  const [targetR, setTargetR] = useState<string>("");
  const [brokerage, setBrokerage] = useState(0);
  const [fees, setFees] = useState(0);
  const [contracts, setContracts] = useState(1);
  const [slip, setSlip] = useState(1);
  const [trainFrac, setTrainFrac] = useState(0.7);
  const [minSample, setMinSample] = useState(30);
  const [result, setResult] = useState<any>(null);
  const [wf, setWf] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cur = useMemo(() => datasets.find((d) => `${d.symbol}|${d.contract}` === ds), [datasets, ds]);

  function body() {
    const params: any = strategy === "second_entry_trend" ? { profile, stop_mode: stopMode } : {};
    if (targetR && strategy === "second_entry_trend") params.target_r = parseFloat(targetR);
    return {
      strategy_id: strategy,
      symbol: cur.symbol, contract: cur.contract, params, train_fraction: trainFrac, min_sample: minSample,
      costs: { contracts, brokerage_per_contract_side: brokerage, exchange_fees_per_contract_side: fees, entry_slippage_ticks: slip, exit_slippage_ticks: slip },
    };
  }
  async function run() {
    setBusy(true); setError(null); setWf(null);
    try { setResult(await post("/api/backtest/run", body())); onRun(); } catch (e: any) { setError(e.message); } finally { setBusy(false); }
  }
  async function runWf() {
    setBusy(true); setError(null);
    const grid = strategy === "second_entry_trend" ? { target_r: [1, 1.5, 2], stop_mode: ["signal_bar", "pullback_low"] }
      : { enable_momentum: [true, false], enable_big_bar: [true, false] };
    try { setWf(await post("/api/backtest/walk-forward", { ...body(), grid, folds: 4 })); }
    catch (e: any) { setError(e.message); } finally { setBusy(false); }
  }

  return (
    <div className="card space-y-3">
      <div className="font-semibold">Backtest (5 min)</div>
      <div className="flex flex-wrap gap-3 items-end text-sm">
        <div className="flex flex-col"><span className="label">Dados</span><DatasetPicker datasets={datasets} value={ds} onChange={setDs} /></div>
        <div className="flex flex-col"><span className="label">Estratégia</span>
          <select className="input" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
            <option value="h2l2_scale_momentum">B · H2/L2 + H3/L3 + momento</option><option value="second_entry_trend">A · Second Entry</option></select></div>
        <div className="flex flex-col"><span className="label">Perfil (só A)</span>
          <select className="input" value={profile} onChange={(e) => setProfile(e.target.value)}><option value="daytrade">Day trade</option><option value="scalp">Scalp</option></select></div>
        <div className="flex flex-col"><span className="label">Stop</span>
          <select className="input" value={stopMode} onChange={(e) => setStopMode(e.target.value)}><option value="signal_bar">Barra de sinal</option><option value="pullback_low">Extremo do pullback</option></select></div>
        <div className="flex flex-col"><span className="label">Alvo (R) — vazio = perfil</span><input className="input w-20" value={targetR} onChange={(e) => setTargetR(e.target.value)} /></div>
        <div className="flex flex-col"><span className="label">Contratos</span><input type="number" className="input w-20" value={contracts} min={1} onChange={(e) => setContracts(+e.target.value)} /></div>
        <div className="flex flex-col"><span className="label">Corretagem R$/contrato/lado</span><input type="number" step="0.01" className="input w-24" value={brokerage} onChange={(e) => setBrokerage(+e.target.value)} /></div>
        <div className="flex flex-col"><span className="label">Emolumentos+taxas R$/contrato/lado</span><input type="number" step="0.01" className="input w-24" value={fees} onChange={(e) => setFees(+e.target.value)} /></div>
        <div className="flex flex-col"><span className="label">Slippage (ticks)</span><input type="number" step="0.5" className="input w-20" value={slip} onChange={(e) => setSlip(+e.target.value)} /></div>
        <div className="flex flex-col"><span className="label">Fração de treino</span><input type="number" step="0.05" className="input w-20" value={trainFrac} onChange={(e) => setTrainFrac(+e.target.value)} /></div>
        <div className="flex flex-col"><span className="label">Amostra mínima</span><input type="number" className="input w-20" value={minSample} onChange={(e) => setMinSample(+e.target.value)} /></div>
        <button className="btn" onClick={run} disabled={!cur || busy}>{busy ? "Executando…" : "Executar backtest"}</button>
        <button className="btn-ghost" onClick={runWf} disabled={!cur || busy}>Walk-forward</button>
      </div>
      <ErrorBox error={error} />
      {result && <BacktestResult r={result} />}
      {wf && <WalkForward wf={wf} />}
    </div>
  );
}

function BacktestResult({ r }: { r: any }) {
  const p = r.probability;
  return (
    <div className="space-y-3">
      <SyntheticBanner show={r.meta.synthetic} />
      <div className="text-xs text-slate-400">
        run_id <b>{r.run_id}</b> · {r.meta.symbol} {r.meta.contracts.join(", ")} · {r.meta.bars} candles · {r.meta.first_ts.slice(0, 10)} a {r.meta.last_ts.slice(0, 10)} · corte treino/teste {r.meta.train_test_cutoff?.slice(0, 16)} ·
        tick {r.meta.tick_size} = R$ {fmt(r.meta.tick_value_brl)} · sinais {r.meta.counters.signals}, acionados {r.meta.counters.filled}, expirados {r.meta.counters.order_expired}
      </div>
      <ul className="list-disc ml-5 text-xs text-amber-300">{r.warnings.map((w: string) => <li key={w}>{w}</li>)}</ul>
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-3">
        <StatsBlock title="Amostra completa" s={r.stats.all} />
        <StatsBlock title="Treino (dentro da amostra)" s={r.stats.train} />
        <StatsBlock title="Teste (fora da amostra)" s={r.stats.test} />
      </div>
      <div className="card">
        <div className="font-semibold mb-1">Probabilidade de evento definido</div>
        <div className="text-sm text-slate-300">{p.event}</div>
        {p.p_hat === null ? <div className="text-amber-300 text-sm mt-1">{p.message}</div> : (
          <div className="grid grid-cols-2 md:grid-cols-5 gap-2 text-sm mt-2">
            <Stat label="Estimativa (treino)" value={pct(p.p_hat)} hint={`IC95% ${ci(p.ci95, true)} · n=${p.n_train}`} />
            <Stat label="Taxa fora da amostra" value={pct(p.test_rate)} hint={`n=${p.n_test}`} />
            <Stat label="Brier (teste)" value={fmt(p.brier_test, 4)} />
            <Stat label="Brier de referência" value={fmt(p.brier_reference, 4)} hint="taxa do próprio teste" />
            <Stat label="Método" value="frequência empírica" />
          </div>
        )}
        {p.message && p.p_hat !== null && <div className="text-xs text-amber-300 mt-1">{p.message}</div>}
      </div>
      <div className="card">
        <div className="font-semibold mb-1">Curva de capital (R$ líquido acumulado)</div>
        <EquityChart points={r.equity_curve} />
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-3">
        <Breakdown title="Por horário (hora do sinal)" data={r.breakdown.by_hour} />
        <Breakdown title="Por motivo de saída" data={r.breakdown.by_exit_reason} />
        <Breakdown title="Por lado / regime" data={{ ...r.breakdown.by_side, ...r.breakdown.by_regime }} />
      </div>
      <div className="card">
        <div className="flex justify-between"><div className="font-semibold mb-1">Operações hipotéticas ({r.trades.length})</div>
          <a className="btn-ghost" href={`${API_URL}/api/backtest/runs/${r.run_id}/trades.csv`}>Exportar CSV</a></div>
        <div className="max-h-72 overflow-auto">
          <table className="tbl">
            <thead><tr><th>#</th><th>Sinal</th><th>Lado</th><th>Entrada</th><th>Stop</th><th>Alvo</th><th>Saída</th><th>Motivo</th><th>Pts</th><th>R$ líq.</th><th>R</th></tr></thead>
            <tbody>
              {r.trades.map((t: any) => (
                <tr key={t.trade_id}>
                  <td>{t.trade_id}</td><td>{t.signal_ts.slice(0, 16).replace("T", " ")}</td><td>{t.side === "long" ? "▲" : "▼"}</td>
                  <td>{fmt(t.entry_price)}</td><td>{fmt(t.stop_price)}</td><td>{fmt(t.target_price)}</td><td>{fmt(t.exit_price)}</td>
                  <td>{t.exit_reason}{t.ambiguous_bar ? " *" : ""}</td><td>{fmt(t.points)}</td>
                  <td className={t.net_brl >= 0 ? "text-green-400" : "text-red-400"}>{fmt(t.net_brl)}</td><td>{fmt(t.net_r, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="text-xs text-slate-500 mt-1">* stop e alvo no mesmo candle: resolvido como stop (política conservadora). {r.disclaimer}</div>
      </div>
    </div>
  );
}

function Breakdown({ title, data }: { title: string; data: Record<string, any> }) {
  return (
    <div className="card">
      <div className="font-semibold mb-1">{title}</div>
      <table className="tbl"><thead><tr><th></th><th>n</th><th>acerto</th><th>R méd.</th><th>R$ líq.</th></tr></thead>
        <tbody>{Object.entries(data).map(([k, v]) => (
          <tr key={k} className={v.sufficient ? "" : "text-slate-500"}><td>{k}</td><td>{v.n}</td><td>{pct(v.win_rate)}</td><td>{fmt(v.expectancy_r, 3)}</td><td>{fmt(v.net_brl)}</td></tr>
        ))}</tbody></table>
      <div className="text-[10px] text-slate-500">Linhas em cinza: amostra abaixo do mínimo.</div>
    </div>
  );
}

function WalkForward({ wf }: { wf: any }) {
  return (
    <div className="card space-y-2">
      <div className="font-semibold">Walk-forward ancorado — {wf.configurations_tested} configurações testadas</div>
      <table className="tbl"><thead><tr><th>Dobra</th><th>Fim treino</th><th>Selecionado</th><th>R treino (n)</th><th>R teste (n)</th><th>R$ teste</th></tr></thead>
        <tbody>{wf.folds.map((f: any) => (
          <tr key={f.fold}><td>{f.fold}</td><td>{f.train_end.slice(0, 10)}</td><td>{f.selected ? JSON.stringify(f.selected) : f.note}</td>
            <td>{fmt(f.train_expectancy_r, 3)} ({f.train_n ?? "-"})</td><td>{fmt(f.test_expectancy_r, 3)} ({f.test_n ?? "-"})</td><td>{fmt(f.test_net_brl)}</td></tr>
        ))}</tbody></table>
      <StatsBlock title="Agregado fora da amostra" s={wf.oos_stats} />
      <div className="text-xs text-slate-400">p-valor (Bonferroni, {wf.configurations_tested} configurações): {fmt(wf.oos_p_value_bonferroni, 4)}. {wf.note}</div>
    </div>
  );
}

function RunsCard() {
  const [runs, setRuns] = useState<any[]>([]);
  useEffect(() => { get<any>("/api/backtest/runs").then((r) => setRuns(r.runs)).catch(() => {}); }, []);
  return (
    <div className="card">
      <div className="font-semibold mb-1">Backtests registrados</div>
      <table className="tbl"><thead><tr><th>run_id</th><th>Ativo</th><th>Perfil</th><th>Período</th><th>n</th><th>R$ líq.</th><th></th></tr></thead>
        <tbody>{runs.map((r) => (
          <tr key={r.run_id}><td className="font-mono text-xs">{r.run_id}</td><td>{r.symbol}</td><td>{r.profile}</td>
            <td>{r.first_ts.slice(0, 10)} a {r.last_ts.slice(0, 10)}</td><td>{r.n}</td><td>{fmt(r.net_brl)}</td><td>{r.synthetic ? "SINTÉTICO" : ""}</td></tr>
        ))}</tbody></table>
    </div>
  );
}
