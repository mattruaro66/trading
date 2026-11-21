import { useEffect, useRef, useState } from "react";
import { createChart, ColorType, type Time } from "lightweight-charts";
import { type AlertDTO, type Criterion, type Dataset, type ExplanationSection, ci, fmt, get, pct } from "../api";

export function SyntheticBanner({ show }: { show: boolean }) {
  if (!show) return null;
  return (
    <div className="bg-amber-900/60 border border-amber-600 text-amber-100 text-sm rounded px-3 py-2">
      <b>DADOS SINTÉTICOS</b> — gerados pelo ATLAS apenas para testar o software. Não representam o mercado real e
      nenhum resultado aqui tem valor estatístico sobre WIN/WDO.
    </div>
  );
}

export function ErrorBox({ error }: { error: string | null }) {
  if (!error) return null;
  return <div className="bg-red-950 border border-red-800 text-red-200 text-sm rounded px-3 py-2">{error}</div>;
}

export function useDatasets() {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const reload = () => get<{ datasets: Dataset[] }>("/api/data/datasets").then((r) => setDatasets(r.datasets)).catch(() => setDatasets([]));
  useEffect(() => {
    reload();
  }, []);
  return { datasets, reload };
}

export function DatasetPicker({ datasets, value, onChange }: { datasets: Dataset[]; value: string; onChange: (k: string) => void }) {
  return (
    <select className="input" value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">— selecione um conjunto de dados —</option>
      {datasets.map((d) => (
        <option key={d.symbol + d.contract} value={`${d.symbol}|${d.contract}`}>
          {d.symbol} {d.contract} · {d.bars} candles · {d.first_ts.slice(0, 10)} a {d.last_ts.slice(0, 10)}
          {d.synthetic ? " · SINTÉTICO" : ""}
        </option>
      ))}
    </select>
  );
}

export function CriteriaList({ criteria }: { criteria: Criterion[] }) {
  return (
    <table className="tbl">
      <tbody>
        {criteria.map((c) => (
          <tr key={c.code}>
            <td className={c.passed ? "text-green-400" : "text-red-400"}>{c.passed ? "✔" : "✘"}</td>
            <td className="text-slate-400">{c.code}</td>
            <td>{c.description}</td>
            <td className="text-slate-300">{fmt(c.value as number, 3)}</td>
            <td className="text-slate-500">{fmt(c.threshold as number, 3)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Explanation({ sections }: { sections: ExplanationSection[] }) {
  return (
    <div className="space-y-3 text-sm">
      {sections.map((s) => (
        <div key={s.title}>
          <div className="font-semibold text-sky-300">{s.title}</div>
          {s.text && <p className="text-slate-300 leading-relaxed">{s.text}</p>}
          {s.items && s.title.startsWith("C") && <CriteriaList criteria={s.items as Criterion[]} />}
          {s.items && s.title.startsWith("B") && (
            <ul className="space-y-2 mt-1">
              {(s.items as any[]).map((it) => (
                <li key={it.concept} className="border-l-2 border-slate-700 pl-2">
                  <div className="font-medium">{it.concept}</div>
                  <div><span className="text-slate-500">Conceito (resumo ATLAS): </span>{it.author_concept}</div>
                  <div><span className="text-slate-500">Interpretação ATLAS: </span>{it.atlas_interpretation}</div>
                  <div><span className="text-slate-500">Regra implementada: </span>{it.quant_rule}</div>
                  <div className="text-slate-500 text-xs">{(it.references as string[]).join("; ")}</div>
                </li>
              ))}
            </ul>
          )}
          {s.items && s.title.startsWith("G") && (
            <ul className="list-disc ml-5 text-slate-300">
              {(s.items as string[]).map((x) => <li key={x}>{x}</li>)}
            </ul>
          )}
        </div>
      ))}
    </div>
  );
}

export function AlertDetail({ alert }: { alert: AlertDTO }) {
  const p = alert.payload;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
        <span><b>{p.symbol}</b> {p.contract}</span>
        <span>{p.timeframe}</span>
        <span>{p.side === "long" ? "Compra hipotética" : "Venda hipotética"} · {p.setup}</span>
        <span>Estado: <b>{alert.state_label ?? alert.state}</b></span>
        <span className={alert.integrity_ok === false ? "text-red-400" : "text-slate-500"}>
          hash {alert.content_hash.slice(0, 12)}…{alert.integrity_ok === false ? " (INTEGRIDADE FALHOU)" : ""}
        </span>
      </div>
      {p.signal && (
        <div className="grid grid-cols-3 md:grid-cols-6 gap-2 text-sm">
          <Stat label="Entrada (stop)" value={fmt(p.signal.entry_price)} />
          <Stat label="Stop" value={fmt(p.signal.stop_price)} />
          <Stat label="Alvo" value={fmt(p.signal.target_price)} />
          <Stat label="Risco (ticks)" value={fmt(p.signal.risk_ticks, 0)} />
          <Stat label="Retorno/risco" value={fmt(p.signal.reward_risk)} />
          <Stat label="Versão" value={`${p.strategy_id} ${p.signal.strategy_version}`} />
        </div>
      )}
      {p.explanation && <Explanation sections={p.explanation.sections} />}
      <div>
        <div className="label mb-1">Histórico imutável de eventos</div>
        <table className="tbl">
          <tbody>
            {alert.events.map((e) => (
              <tr key={e.seq}>
                <td className="text-slate-500">{e.seq}</td>
                <td>{e.ts.slice(0, 16).replace("T", " ")}</td>
                <td>{e.state_label ?? e.state}</td>
                <td className="text-slate-300">{e.reason}</td>
                <td className="text-slate-600 text-xs">{e.event_hash.slice(0, 10)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="bg-slate-800/60 rounded px-2 py-1">
      <div className="label">{label}</div>
      <div className="font-medium">{value}</div>
      {hint && <div className="text-[10px] text-slate-500">{hint}</div>}
    </div>
  );
}

export function StatsBlock({ title, s }: { title: string; s: any }) {
  return (
    <div className="card space-y-2">
      <div className="font-semibold">{title}</div>
      {!s.sufficient && <div className="text-amber-300 text-xs">{s.message}</div>}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-sm">
        <Stat label="Operações" value={fmt(s.n, 0)} />
        <Stat label="Taxa de acerto" value={pct(s.win_rate)} hint={`IC95% ${ci(s.win_rate_ci95, true)}`} />
        <Stat label="Expectativa (R líq.)" value={fmt(s.expectancy_r, 3)} hint={`IC95% ${ci(s.expectancy_r_ci95)}`} />
        <Stat label="Expectativa (R$)" value={fmt(s.expectancy_brl)} hint={`IC95% ${ci(s.expectancy_brl_ci95)}`} />
        <Stat label="Payoff" value={fmt(s.payoff)} />
        <Stat label="Profit factor" value={fmt(s.profit_factor)} />
        <Stat label="Resultado líquido" value={`R$ ${fmt(s.net_brl)}`} hint={`bruto R$ ${fmt(s.gross_brl)} · custos R$ ${fmt(s.costs_brl)}`} />
        <Stat label="Drawdown máx." value={`R$ ${fmt(s.max_drawdown_brl)}`} hint={`perdas seguidas: ${s.max_consecutive_losses}`} />
      </div>
      <div className="text-xs text-slate-500">t = {fmt(s.t_stat, 2)} · p(média R &gt; 0) = {fmt(s.p_value_mean_gt_0, 4)}</div>
    </div>
  );
}

export function EquityChart({ points }: { points: { ts: string; equity_brl: number }[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const c = createChart(ref.current, {
      height: 220, autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "#0f172a" }, textColor: "#cbd5e1" },
      grid: { vertLines: { color: "#1e293b" }, horzLines: { color: "#1e293b" } },
      timeScale: { timeVisible: true },
    });
    const s = c.addLineSeries({ color: "#38bdf8", lineWidth: 2 });
    const seen = new Set<number>();
    const data = points
      .map((p) => ({ time: Math.floor(new Date(p.ts).getTime() / 1000) as number, value: p.equity_brl }))
      .filter((d) => (seen.has(d.time) ? false : (seen.add(d.time), true)));
    s.setData(data.map((d) => ({ time: d.time as Time, value: d.value })));
    c.timeScale().fitContent();
    return () => c.remove();
  }, [points]);
  return <div ref={ref} style={{ height: 220 }} />;
}
