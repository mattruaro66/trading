import { useState } from "react";
import Lab from "./pages/Lab";
import Replay from "./pages/Replay";
import { Alerts, Dashboard, Live, Methodology } from "./pages/Others";

const TABS = [
  { id: "dash", label: "Painel" },
  { id: "replay", label: "Replay" },
  { id: "lab", label: "Laboratório" },
  { id: "alerts", label: "Alertas" },
  { id: "live", label: "Dados ao vivo" },
  { id: "method", label: "Metodologia" },
] as const;

export default function App() {
  const [tab, setTab] = useState<(typeof TABS)[number]["id"]>("dash");
  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-800 bg-slate-900/80 px-4 py-2 flex items-center gap-6">
        <div>
          <div className="font-bold tracking-wide">ATLAS B3</div>
          <div className="text-[10px] text-slate-500">WIN · WDO · 5 minutos</div>
        </div>
        <nav className="flex gap-1">
          {TABS.map((t) => (
            <button key={t.id} onClick={() => setTab(t.id)}
              className={`px-3 py-1.5 rounded text-sm ${tab === t.id ? "bg-slate-700 text-white" : "text-slate-400 hover:text-white"}`}>
              {t.label}
            </button>
          ))}
        </nav>
        <div className="ml-auto text-xs text-slate-400 text-right">
          Pesquisa, monitoramento e simulação.<br /><b className="text-slate-300">Não executa ordens.</b>
        </div>
      </header>
      <main className="p-4">
        {tab === "dash" && <Dashboard />}
        {tab === "replay" && <Replay />}
        {tab === "lab" && <Lab />}
        {tab === "alerts" && <Alerts />}
        {tab === "live" && <Live />}
        {tab === "method" && <Methodology />}
      </main>
    </div>
  );
}
