import React, { useState, useEffect, useRef } from 'react';
import { Activity, Layers, DownloadCloud, Clock, RefreshCcw } from 'lucide-react';
import { createChart, IChartApi, ISeriesApi, CandlestickData, LineData, HistogramData } from 'lightweight-charts';

interface Bar {
  ts: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export const PriceChartPanel: React.FC = () => {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candlestickSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const ema9SeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const ema20SeriesRef = useRef<ISeriesApi<"Line"> | null>(null);

  // Sub-chart Oscillator Refs
  const oscillatorContainerRef = useRef<HTMLDivElement>(null);
  const oscillatorChartRef = useRef<IChartApi | null>(null);
  const rsiSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const macdLineSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const macdSignalSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const macdHistSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);

  const [timeframe, setTimeframe] = useState<string>('1m');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Default date range (from 12 days ago to today) to cleanly cover June 29th onwards
  const [startDate, setStartDate] = useState<string>(() => {
    const d = new Date(); d.setDate(d.getDate() - 12); return d.toISOString().split('T')[0];
  });
  const [endDate, setEndDate] = useState<string>(() => new Date().toISOString().split('T')[0]);

  // Stored Symbols State
  const [symbols, setSymbols] = useState<string[]>(['NIFTY']);
  const [selectedSymbol, setSelectedSymbol] = useState<string>('NIFTY');

  // Loaded bars cache for CSV export
  const [loadedBars, setLoadedBars] = useState<Bar[]>([]);

  // Overlays state
  const [showOverlays, setShowOverlays] = useState(true);
  const [activeIndicators, setActiveIndicators] = useState<string[]>(['EMA']); // can contain 'EMA', 'RSI', 'MACD'
  const [showIndicatorMenu, setShowIndicatorMenu] = useState(false);
  const [captures, setCaptures] = useState<any[]>([]);
  const [portfolio, setPortfolio] = useState<any[]>([]);

  // Exponential Moving Average helper
  const calculateEMA = (data: { time: number; close: number }[], period: number) => {
    const emaData: { time: number; value: number }[] = [];
    if (data.length === 0) return emaData;

    const multiplier = 2 / (period + 1);
    let prevEma = data[0].close;
    emaData.push({ time: data[0].time, value: prevEma });

    for (let i = 1; i < data.length; i++) {
      const curEma = (data[i].close - prevEma) * multiplier + prevEma;
      emaData.push({ time: data[i].time, value: curEma });
      prevEma = curEma;
    }
    return emaData;
  };

  // Relative Strength Index helper (14-period RSI)
  const calculateRSI = (data: { time: number; close: number }[], period = 14) => {
    const rsiData: { time: number; value: number }[] = [];
    if (data.length <= period) return rsiData;

    let avgGain = 0;
    let avgLoss = 0;

    for (let i = 1; i <= period; i++) {
      const change = data[i].close - data[i - 1].close;
      if (change > 0) avgGain += change;
      else avgLoss += Math.abs(change);
    }

    avgGain /= period;
    avgLoss /= period;

    let rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
    let rsi = 100 - 100 / (1 + rs);
    rsiData.push({ time: data[period].time, value: rsi });

    for (let i = period + 1; i < data.length; i++) {
      const change = data[i].close - data[i - 1].close;
      const gain = change > 0 ? change : 0;
      const loss = change < 0 ? Math.abs(change) : 0;

      avgGain = (avgGain * (period - 1) + gain) / period;
      avgLoss = (avgLoss * (period - 1) + loss) / period;

      rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
      rsi = 100 - 100 / (1 + rs);
      rsiData.push({ time: data[i].time, value: rsi });
    }
    return rsiData;
  };

  // MACD helper (12, 26, 9)
  const calculateMACD = (data: { time: number; close: number }[]) => {
    const macdData: { time: number; macd: number; signal: number; hist: number }[] = [];
    if (data.length < 26) return macdData;

    const ema12 = calculateEMA(data, 12);
    const ema26 = calculateEMA(data, 26);

    const macdLineRaw: { time: number; value: number }[] = [];
    for (let i = 0; i < data.length; i++) {
      const t = data[i].time;
      const val12 = ema12.find(d => d.time === t)?.value;
      const val26 = ema26.find(d => d.time === t)?.value;
      if (val12 !== undefined && val26 !== undefined) {
        macdLineRaw.push({ time: t, value: val12 - val26 });
      }
    }

    const signalLineRaw = calculateEMA(macdLineRaw, 9);

    for (let i = 0; i < macdLineRaw.length; i++) {
      const t = macdLineRaw[i].time;
      const mVal = macdLineRaw[i].value;
      const sVal = signalLineRaw.find(d => d.time === t)?.value;
      if (sVal !== undefined) {
        macdData.push({
          time: t,
          macd: mVal,
          signal: sVal,
          hist: mVal - sVal
        });
      }
    }
    return macdData;
  };

  useEffect(() => {
    // Initialize chart
    if (chartContainerRef.current && !chartRef.current) {
      const chart = createChart(chartContainerRef.current, {
        layout: {
          background: { color: 'transparent' },
          textColor: '#64748b',
        },
        grid: {
          vertLines: { color: '#f1f5f9' },
          horzLines: { color: '#f1f5f9' },
        },
        timeScale: {
          timeVisible: true,
          secondsVisible: false,
          rightOffset: 12,
        },
        crosshair: {
          mode: 1, // Normal mode
        }
      });

      const candlestickSeries = chart.addCandlestickSeries({
        upColor: '#10b981',
        downColor: '#ef4444',
        borderVisible: false,
        wickUpColor: '#10b981',
        wickDownColor: '#ef4444',
      });

      const volumeSeries = chart.addHistogramSeries({
        color: '#e2e8f0',
        priceFormat: { type: 'volume' },
        priceScaleId: '', // set as an overlay by setting a blank priceScaleId
      });
      volumeSeries.priceScale().applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
      });

      const ema9Series = chart.addLineSeries({
        color: '#3b82f6', // blue
        lineWidth: 1.5,
        title: 'EMA 9',
      });

      const ema20Series = chart.addLineSeries({
        color: '#f97316', // orange
        lineWidth: 1.5,
        title: 'EMA 20',
      });

      const rsiSeries = chart.addLineSeries({
        color: '#8b5cf6', // purple
        lineWidth: 1.5,
        title: 'RSI',
        priceScaleId: 'left',
      });

      const macdLineSeries = chart.addLineSeries({
        color: '#2563eb', // blue
        lineWidth: 1.5,
        title: 'MACD',
        priceScaleId: 'left',
      });

      const macdSignalSeries = chart.addLineSeries({
        color: '#f97316', // orange
        lineWidth: 1.5,
        title: 'Sig',
        priceScaleId: 'left',
      });

      const macdHistSeries = chart.addHistogramSeries({
        priceScaleId: 'left',
      });

      chartRef.current = chart;
      candlestickSeriesRef.current = candlestickSeries;
      volumeSeriesRef.current = volumeSeries;
      ema9SeriesRef.current = ema9Series;
      ema20SeriesRef.current = ema20Series;
      rsiSeriesRef.current = rsiSeries;
      macdLineSeriesRef.current = macdLineSeries;
      macdSignalSeriesRef.current = macdSignalSeries;
      macdHistSeriesRef.current = macdHistSeries;

      const resizeObserver = new ResizeObserver((entries) => {
        if (entries[0] && chartContainerRef.current) {
          const width = chartContainerRef.current.clientWidth;
          // Apply height if container has specified height or default to 500
          chart.resize(width || 300, 500);
        }
      });
      if (chartContainerRef.current) {
        resizeObserver.observe(chartContainerRef.current);
      }

      // Load initial data
      fetchSymbols();
      fetchOverlayData();

      return () => {
        resizeObserver.disconnect();
        chart.remove();
        chartRef.current = null;
      };
    }
  }, []);

  useEffect(() => {
    if (chartRef.current) {
      fetchBars(timeframe, selectedSymbol);
    }
  }, [timeframe, selectedSymbol, startDate, endDate]);

  useEffect(() => {
    if (!chartRef.current || loadedBars.length === 0) return;

    // 1. Prepare common candlestick time-series array
    const candleData: CandlestickData[] = [];
    const seenTimes = new Set<string | number>();
    const sorted = [...loadedBars].sort((a, b) => new Date(a.ts).getTime() - new Date(b.ts).getTime());
    
    for (const bar of sorted) {
      let time: any;
      if (timeframe === '1d') {
        time = bar.ts.split('T')[0];
      } else {
        const utcEpoch = new Date(bar.ts).getTime() / 1000;
        time = utcEpoch + 19800;
      }
      if (seenTimes.has(time)) continue;
      seenTimes.add(time);
      
      candleData.push({
        time,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close
      });
    }

    // 2. Toggle Left Price Scale visibility based on selection
    chartRef.current.priceScale('left').applyOptions({
      visible: activeIndicators.includes('RSI') || activeIndicators.includes('MACD'),
    });

    // 3. Render EMA 9 / 20
    if (activeIndicators.includes('EMA')) {
      const ema9Data = calculateEMA(candleData, 9);
      const ema20Data = calculateEMA(candleData, 20);
      ema9SeriesRef.current?.setData(ema9Data);
      ema20SeriesRef.current?.setData(ema20Data);
    } else {
      ema9SeriesRef.current?.setData([]);
      ema20SeriesRef.current?.setData([]);
    }

    // 4. Render RSI (14) directly on main chart (using left scale)
    if (activeIndicators.includes('RSI')) {
      const rsiData = calculateRSI(candleData, 14);
      rsiSeriesRef.current?.setData(rsiData);
    } else {
      rsiSeriesRef.current?.setData([]);
    }

    // 5. Render MACD directly on main chart (using left scale)
    if (activeIndicators.includes('MACD')) {
      const macdData = calculateMACD(candleData);
      const lineData: LineData[] = macdData.map(d => ({ time: d.time, value: d.macd }));
      const sigData: LineData[] = macdData.map(d => ({ time: d.time, value: d.signal }));
      const histData: HistogramData[] = macdData.map(d => ({
        time: d.time,
        value: d.hist,
        color: d.hist >= 0 ? 'rgba(16, 185, 129, 0.4)' : 'rgba(239, 68, 68, 0.4)'
      }));

      macdLineSeriesRef.current?.setData(lineData);
      macdSignalSeriesRef.current?.setData(sigData);
      macdHistSeriesRef.current?.setData(histData);
    } else {
      macdLineSeriesRef.current?.setData([]);
      macdSignalSeriesRef.current?.setData([]);
      macdHistSeriesRef.current?.setData([]);
    }
  }, [activeIndicators, loadedBars, timeframe]);

  const fetchSymbols = async () => {
    try {
      const res = await fetch('/api/bars/symbols');
      const json = await res.json();
      if (json.success && json.symbols.length > 0) {
        setSymbols(json.symbols);
        // Default to first stored symbol if current is not available
        if (!json.symbols.includes(selectedSymbol)) {
          setSelectedSymbol(json.symbols[0]);
        }
      }
    } catch (e) {
      console.error("Failed to load symbols", e);
    }
  };

  const fetchOverlayData = async () => {
    try {
      const capRes = await fetch('/api/captures');
      const capJson = await capRes.json();
      if (capJson.success) setCaptures(capJson.captures || []);

      const portRes = await fetch('/api/portfolio');
      const portJson = await portRes.json();
      if (portJson.success) setPortfolio(portJson.data || []);
    } catch (e) {
      console.error("Failed to load overlays", e);
    }
  };

  const downloadBarsCSV = () => {
    if (loadedBars.length === 0) return;
    const headers = ["Timestamp", "Open", "High", "Low", "Close", "Volume"];
    const sorted = [...loadedBars].sort((a, b) => new Date(a.ts).getTime() - new Date(b.ts).getTime());
    const csvContent = [
      headers.join(","),
      ...sorted.map(b => [
        b.ts, b.open, b.high, b.low, b.close, b.volume
      ].join(","))
    ].join("\n");

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement("a");
    const url = URL.createObjectURL(blob);
    link.setAttribute("href", url);
    link.setAttribute("download", `${selectedSymbol}_price_bars_${timeframe}.csv`);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const fetchBars = async (tf: string, sym: string) => {
    setLoading(true);
    setError(null);
    try {
      const startParam = startDate ? `&start=${startDate}T00:00:00.000Z` : '';
      const endParam = endDate ? `&end=${endDate}T23:59:59.000Z` : '';
      const res = await fetch(`/api/bars?symbol=${encodeURIComponent(sym)}&tf=${tf}${startParam}${endParam}`);
      const json = await res.json();
      if (json.success && json.data.length > 0) {
        setLoadedBars(json.data);
        // Convert to Lightweight Charts format
        const candleData: CandlestickData[] = [];
        const volData: HistogramData[] = [];

        // Data must be strictly ascending by time
        const sorted = [...json.data].sort((a, b) => new Date(a.ts).getTime() - new Date(b.ts).getTime());

        const seenTimes = new Set<string | number>();

        for (const bar of sorted) {
          // Fix timezone formatting for the chart
          let time: any;
          if (tf === '1d') {
            // Daily bars require 'yyyy-mm-dd' string format in lightweight-charts
            time = bar.ts.split('T')[0];
          } else {
            // Intraday bars use Unix timestamp. The library renders in UTC, so we add the IST offset
            // IST is UTC+5:30, which is 19800 seconds
            const utcEpoch = new Date(bar.ts).getTime() / 1000;
            time = utcEpoch + 19800;
          }

          if (seenTimes.has(time)) {
            continue; // Skip duplicate timestamps to prevent chart crashes
          }
          seenTimes.add(time);

          candleData.push({
            time,
            open: bar.open,
            high: bar.high,
            low: bar.low,
            close: bar.close
          });
          volData.push({
            time,
            value: bar.volume,
            color: bar.close >= bar.open ? 'rgba(16, 185, 129, 0.3)' : 'rgba(239, 68, 68, 0.3)'
          });
        }

        candlestickSeriesRef.current?.setData(candleData);
        volumeSeriesRef.current?.setData(volData);

        if (activeIndicators.includes('EMA')) {
          const ema9Data = calculateEMA(candleData, 9);
          const ema20Data = calculateEMA(candleData, 20);
          ema9SeriesRef.current?.setData(ema9Data);
          ema20SeriesRef.current?.setData(ema20Data);
        } else {
          ema9SeriesRef.current?.setData([]);
          ema20SeriesRef.current?.setData([]);
        }

        if (activeIndicators.includes('RSI')) {
          const rsiData = calculateRSI(candleData, 14);
          rsiSeriesRef.current?.setData(rsiData);
        } else {
          rsiSeriesRef.current?.setData([]);
        }

        if (activeIndicators.includes('MACD')) {
          const macdData = calculateMACD(candleData);
          const lineData = macdData.map(d => ({ time: d.time, value: d.macd }));
          const sigData = macdData.map(d => ({ time: d.time, value: d.signal }));
          const histData = macdData.map(d => ({
            time: d.time,
            value: d.hist,
            color: d.hist >= 0 ? 'rgba(16, 185, 129, 0.4)' : 'rgba(239, 68, 68, 0.4)'
          }));
          macdLineSeriesRef.current?.setData(lineData);
          macdSignalSeriesRef.current?.setData(sigData);
          macdHistSeriesRef.current?.setData(histData);
        } else {
          macdLineSeriesRef.current?.setData([]);
          macdSignalSeriesRef.current?.setData([]);
          macdHistSeriesRef.current?.setData([]);
        }

        chartRef.current?.priceScale('left').applyOptions({
          visible: activeIndicators.includes('RSI') || activeIndicators.includes('MACD'),
        });

        chartRef.current?.timeScale().fitContent();

        if (showOverlays) applyOverlays(candleData);
      } else if (json.data.length === 0) {
        setLoadedBars([]);
        setError("No bars found in database. Run the backfill script first.");
      } else {
        setLoadedBars([]);
        setError(json.detail || "Failed to fetch bars");
      }
    } catch (e: any) {
      setLoadedBars([]);
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const applyOverlays = (candleData: CandlestickData[]) => {
    const series = candlestickSeriesRef.current;
    if (!series) return;

    // Clear previous markers (permanently disabled Capture ID overlays)
    series.setMarkers([]);
  };

  useEffect(() => {
    if (candlestickSeriesRef.current && loadedBars.length > 0) {
      if (showOverlays) {
        const candleData = loadedBars.map(bar => {
          let time: any;
          if (timeframe === '1d') {
            time = bar.ts.split('T')[0];
          } else {
            const utcEpoch = new Date(bar.ts).getTime() / 1000;
            time = utcEpoch + 19800;
          }
          return { time, open: bar.open, high: bar.high, low: bar.low, close: bar.close };
        });
        applyOverlays(candleData);
      } else {
        candlestickSeriesRef.current.setMarkers([]);
      }
    }
  }, [showOverlays]);

  return (
    <div className="bg-white rounded-2xl p-8 border border-slate-200 shadow-sm mt-6">
      {/* Title Header */}
      <div className="mb-6 border-b border-slate-100 pb-4">
        <h3 className="text-xl font-black text-slate-800 flex items-center gap-2">
          <Activity className="w-5 h-5 text-blue-500" /> Professional Price Chart
        </h3>
        <p className="text-sm text-slate-500 mt-1">Ground truth 1m and 1d bars, resampled natively.</p>
      </div>

      {/* Responsive Controls Bar */}
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4 mb-6 p-4 bg-slate-50 border border-slate-200 rounded-2xl">
        <div className="flex flex-wrap items-center gap-4">
          {/* Symbol Selector Dropdown */}
          <div className="flex items-center gap-2 bg-white px-3 py-1.5 border border-slate-200 rounded-xl shadow-sm">
            <span className="text-xs font-black text-slate-400 uppercase tracking-wider">Symbol:</span>
            <select
              value={selectedSymbol}
              onChange={(e) => setSelectedSymbol(e.target.value)}
              className="text-sm font-bold bg-transparent outline-none text-slate-700 cursor-pointer"
            >
              {symbols.map(sym => {
                let displayLabel = sym;
                if (sym === 'NIFTY_FUT_1' || sym === 'NIFTY_FUT_2') {
                  const refDate = new Date();
                  const lastThursday = (year: number, month: number) => {
                    const lastDay = new Date(year, month + 1, 0);
                    while (lastDay.getDay() !== 4) {
                      lastDay.setDate(lastDay.getDate() - 1);
                    }
                    return lastDay;
                  };
                  const currExp = lastThursday(refDate.getFullYear(), refDate.getMonth());
                  let exp: Date;
                  if (refDate.getTime() > currExp.getTime()) {
                    if (sym === 'NIFTY_FUT_1') {
                      const m1 = new Date(refDate.getFullYear(), refDate.getMonth() + 1, 1);
                      exp = lastThursday(m1.getFullYear(), m1.getMonth());
                    } else {
                      const m2 = new Date(refDate.getFullYear(), refDate.getMonth() + 2, 1);
                      exp = lastThursday(m2.getFullYear(), m2.getMonth());
                    }
                  } else {
                    if (sym === 'NIFTY_FUT_1') {
                      exp = currExp;
                    } else {
                      const m2 = new Date(refDate.getFullYear(), refDate.getMonth() + 1, 1);
                      exp = lastThursday(m2.getFullYear(), m2.getMonth());
                    }
                  }
                  const formatStr = `${exp.getDate()}-${exp.toLocaleString('en-US', { month: 'short' })}`;
                  displayLabel = sym === 'NIFTY_FUT_1' 
                    ? `NIFTY FUT Near (${formatStr})` 
                    : `NIFTY FUT Next (${formatStr})`;
                }
                return (
                  <option key={sym} value={sym}>{displayLabel}</option>
                );
              })}
            </select>
            <input
              type="text"
              placeholder="+ Custom"
              className="w-20 px-2 py-0.5 text-xs font-semibold bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-slate-700 uppercase"
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  const val = e.currentTarget.value.trim().toUpperCase();
                  if (val) {
                    if (!symbols.includes(val)) {
                      setSymbols(prev => [...prev, val]);
                    }
                    setSelectedSymbol(val);
                    e.currentTarget.value = '';
                  }
                }
              }}
            />
          </div>

          {/* Date Picker Controls */}
          <div className="flex items-center gap-2 bg-white px-3 py-1.5 border border-slate-200 rounded-xl shadow-sm">
            <span className="text-xs font-bold text-slate-500">From:</span>
            <input 
              type="date" 
              value={startDate} 
              onChange={(e) => setStartDate(e.target.value)} 
              className="bg-transparent text-xs font-bold text-slate-700 outline-none w-28 cursor-pointer"
            />
            <span className="text-xs font-bold text-slate-500 border-l border-slate-200 pl-2">To:</span>
            <input 
              type="date" 
              value={endDate} 
              onChange={(e) => setEndDate(e.target.value)} 
              className="bg-transparent text-xs font-bold text-slate-700 outline-none w-28 cursor-pointer"
            />
          </div>


          {/* Custom Multi-Select Indicator Dropdown */}
          <div className="relative">
            <button
              onClick={() => setShowIndicatorMenu(!showIndicatorMenu)}
              className="flex items-center gap-2 bg-white px-3 py-1.5 border border-slate-200 rounded-xl shadow-sm text-sm font-bold text-slate-700 hover:bg-slate-50 cursor-pointer"
            >
              <span className="text-xs font-black text-slate-400 uppercase tracking-wider">Indicators:</span>
              <span className="truncate max-w-[120px]">
                {activeIndicators.length === 0 ? 'None' : activeIndicators.join(', ')}
              </span>
              <span className="text-[10px] text-slate-400">▼</span>
            </button>

            {showIndicatorMenu && (
              <>
                <div 
                  className="fixed inset-0 z-20" 
                  onClick={() => setShowIndicatorMenu(false)} 
                />
                <div className="absolute left-0 mt-2 w-56 bg-white border border-slate-200 rounded-xl shadow-xl z-30 p-2 flex flex-col gap-1">
                  {[
                    { id: 'EMA', label: 'EMA 9 / 20' },
                    { id: 'RSI', label: 'RSI (14)' },
                    { id: 'MACD', label: 'MACD (12, 26, 9)' }
                  ].map(ind => {
                    const active = activeIndicators.includes(ind.id);
                    return (
                      <label 
                        key={ind.id} 
                        className="flex items-center gap-2.5 px-3 py-2 hover:bg-slate-50 rounded-lg cursor-pointer text-xs font-bold text-slate-600 select-none"
                      >
                        <input
                          type="checkbox"
                          checked={active}
                          onChange={() => {
                            setActiveIndicators(prev => 
                              prev.includes(ind.id)
                                ? prev.filter(x => x !== ind.id)
                                : [...prev, ind.id]
                            );
                          }}
                          className="rounded text-blue-600 focus:ring-blue-500 w-4 h-4 border-slate-300 cursor-pointer"
                        />
                        <span>{ind.label}</span>
                      </label>
                    );
                  })}
                </div>
              </>
            )}
          </div>
        </div>

        {/* Timeframe & Export Buttons */}
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-1 bg-white p-1 rounded-xl border border-slate-200 shadow-sm">
            {['1m', '5m', '15m', '1d'].map(tf => (
              <button
                key={tf}
                onClick={() => setTimeframe(tf)}
                className={`px-3 py-1.5 text-xs font-black rounded-lg transition-all ${timeframe === tf
                    ? 'bg-blue-600 text-white shadow-md'
                    : 'text-slate-500 hover:text-slate-800 hover:bg-slate-50'
                  }`}
              >
                {tf}
              </button>
            ))}
          </div>

          {loadedBars.length > 0 && (
            <button
              onClick={downloadBarsCSV}
              className="px-3.5 py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-black rounded-xl transition flex items-center gap-1.5 shadow-md shadow-emerald-500/20"
            >
              <DownloadCloud className="w-3.5 h-3.5" /> Export CSV
            </button>
          )}
        </div>
      </div>

      {error && (
        <div className="mb-4 bg-red-50 text-red-600 p-3 rounded-lg text-sm font-semibold">
          {error}
        </div>
      )}

      {/* Chart Container */}
      <div
        ref={chartContainerRef}
        className="w-full h-[500px] border border-slate-200 rounded-xl overflow-hidden relative shadow-inner bg-slate-50"
      >
        {loading && (
          <div className="absolute inset-0 bg-white/70 flex items-center justify-center z-10">
            <div className="flex flex-col items-center">
              <RefreshCcw className="w-8 h-8 text-blue-500 animate-spin mb-2" />
              <span className="text-slate-600 font-bold">Resampling & Loading...</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
