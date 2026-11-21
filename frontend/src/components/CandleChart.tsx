import { useEffect, useRef } from "react";
import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  type IPriceLine,
  type SeriesMarker,
  type Time,
  ColorType,
  LineStyle,
} from "lightweight-charts";
import { type BarDTO, chartTime } from "../api";

export interface ChartMarker {
  ts_open: string;
  time: number;
  position: "aboveBar" | "belowBar";
  color: string;
  text: string;
  shape?: "arrowUp" | "arrowDown" | "circle" | "square";
}

export interface ChartLevel {
  price: number;
  color: string;
  title: string;
}

interface Props {
  bars: BarDTO[];
  forming?: BarDTO | null;
  markers?: ChartMarker[];
  levels?: ChartLevel[];
  height?: number;
  follow?: boolean;
}

/** Gráfico de candles de 5 minutos (horário America/Sao_Paulo) com volume, marcações e níveis hipotéticos. */
export default function CandleChart({ bars, forming, markers = [], levels = [], height = 420, follow = true }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const candles = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volume = useRef<ISeriesApi<"Histogram"> | null>(null);
  const lines = useRef<IPriceLine[]>([]);

  useEffect(() => {
    if (!ref.current) return;
    const c = createChart(ref.current, {
      height,
      layout: { background: { type: ColorType.Solid, color: "#0f172a" }, textColor: "#cbd5e1" },
      grid: { vertLines: { color: "#1e293b" }, horzLines: { color: "#1e293b" } },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: "#334155" },
      rightPriceScale: { borderColor: "#334155" },
      crosshair: { mode: 0 },
      autoSize: true,
    });
    candles.current = c.addCandlestickSeries({
      upColor: "#16a34a", downColor: "#dc2626", borderVisible: false, wickUpColor: "#16a34a", wickDownColor: "#dc2626",
    });
    volume.current = c.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, color: "#334155" });
    c.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    chart.current = c;
    return () => {
      c.remove();
      chart.current = null;
    };
  }, [height]);

  useEffect(() => {
    if (!candles.current || !volume.current) return;
    const all = forming && (!bars.length || forming.time > bars[bars.length - 1].time) ? [...bars, forming] : bars;
    candles.current.setData(
      all.map((b) => ({
        time: chartTime(b) as Time, open: b.open, high: b.high, low: b.low, close: b.close,
        ...(b.status === "forming" ? { color: "#64748b", wickColor: "#64748b" } : {}),
      })),
    );
    volume.current.setData(
      all.map((b) => ({ time: chartTime(b) as Time, value: b.volume ?? 0, color: b.close >= b.open ? "#14532d" : "#7f1d1d" })),
    );
    const ms: SeriesMarker<Time>[] = [...markers]
      .sort((a, b) => a.time - b.time)
      .map((m) => ({ time: chartTime(m) as Time, position: m.position, color: m.color, text: m.text, shape: m.shape ?? "circle" }));
    candles.current.setMarkers(ms);
    if (follow && all.length) chart.current?.timeScale().scrollToRealTime();
  }, [bars, forming, markers, follow]);

  useEffect(() => {
    if (!candles.current) return;
    lines.current.forEach((l) => candles.current?.removePriceLine(l));
    lines.current = levels.map((l) =>
      candles.current!.createPriceLine({ price: l.price, color: l.color, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: l.title }),
    );
  }, [levels]);

  return <div ref={ref} style={{ height }} className="w-full" />;
}
