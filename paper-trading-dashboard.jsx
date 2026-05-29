import { useState, useMemo } from "react";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar, ReferenceLine,
} from "recharts";

const FILTER_LABELS = {
  volume_spike:    "Volume Spike (>1.5x)",
  rsi_healthy:     "RSI Healthy (40-70)",
  adx_strong:      "ADX Strong (>20)",
  sector_strength: "Sector Strength",
  no_earnings:     "No Earnings (5d)",
  base_quality:    "Base Quality",
  market_bullish:  "Market Bullish",
};

const SAMPLE_CLOSED = [
  { id: 1, symbol: "RELIANCE", entry_date: "2026-04-15", exit_date: "2026-04-22", entry: 1340, exit: 1395, sl: 1310, t1: 1395, qty: 333, setup: "Flat base breakout", reason: "T1 hit", status: "CLOSED",
    filters: { volume_spike: true, rsi_healthy: true, adx_strong: true, sector_strength: true, no_earnings: true, base_quality: true, market_bullish: true } },
  { id: 2, symbol: "TCS", entry_date: "2026-04-16", exit_date: "2026-04-18", entry: 4200, exit: 4110, sl: 4110, t1: 4335, qty: 119, setup: "EMA crossover", reason: "SL hit", status: "CLOSED",
    filters: { volume_spike: false, rsi_healthy: true, adx_strong: false, sector_strength: true, no_earnings: true, base_quality: true, market_bullish: false } },
  { id: 3, symbol: "INFY", entry_date: "2026-04-17", exit_date: "2026-04-25", entry: 1820, exit: 1925, sl: 1785, t1: 1872, qty: 286, setup: "RSI pullback", reason: "T2 hit", status: "CLOSED",
    filters: { volume_spike: true, rsi_healthy: true, adx_strong: true, sector_strength: true, no_earnings: true, base_quality: true, market_bullish: true } },
  { id: 4, symbol: "TATAMOTORS", entry_date: "2026-04-21", exit_date: "2026-04-28", entry: 850, exit: 892, sl: 832, t1: 877, qty: 277, setup: "Bull flag", reason: "T1 hit", status: "CLOSED",
    filters: { volume_spike: true, rsi_healthy: true, adx_strong: true, sector_strength: true, no_earnings: true, base_quality: true, market_bullish: true } },
  { id: 5, symbol: "WIPRO", entry_date: "2026-04-25", exit_date: "2026-04-26", entry: 540, exit: 525, sl: 525, t1: 558, qty: 333, setup: "Breakout", reason: "SL hit", status: "CLOSED",
    filters: { volume_spike: false, rsi_healthy: true, adx_strong: false, sector_strength: false, no_earnings: true, base_quality: false, market_bullish: false } },
];

const SAMPLE_WATCHLIST = [
  { id: "w1", symbol: "BHARTIARTL", entry: 1580, sl: 1540, t1: 1640, t2: 1700, qty: 125, setup: "Flat base breakout", added: "2026-05-18",
    filters: { volume_spike: true, rsi_healthy: true, adx_strong: true, sector_strength: true, no_earnings: true, base_quality: true, market_bullish: true } },
  { id: "w2", symbol: "ASIANPAINT", entry: 2890, sl: 2820, t1: 2995, t2: 3100, qty: 71, setup: "EMA crossover", added: "2026-05-19",
    filters: { volume_spike: true, rsi_healthy: true, adx_strong: false, sector_strength: true, no_earnings: false, base_quality: true, market_bullish: true } },
];

export default function App() {
  const [view, setView] = useState("watchlist");
  const [watchlist, setWatchlist] = useState(SAMPLE_WATCHLIST);
  const [openTrades, setOpenTrades] = useState([]);
  const [closedTrades, setClosedTrades] = useState(SAMPLE_CLOSED);
  const [selectedTrade, setSelectedTrade] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({
    symbol: "", entry: "", sl: "", t1: "", t2: "", qty: "", setup: "Flat base breakout",
    filters: {
      volume_spike: false, rsi_healthy: false, adx_strong: false,
      sector_strength: false, no_earnings: false, base_quality: false, market_bullish: false,
    },
  });

  // === BUSINESS LOGIC ===
  const addToWatchlist = () => {
    if (!form.symbol || !form.entry || !form.sl || !form.t1 || !form.qty) {
      alert("Please fill: Symbol, Entry, SL, T1, and Qty (required)");
      return;
    }
    const entry = parseFloat(form.entry);
    const sl = parseFloat(form.sl);
    const t1 = parseFloat(form.t1);
    const t2 = parseFloat(form.t2) || (entry + (entry - sl) * 2.67);
    const newItem = {
      id: `w${Date.now()}`,
      symbol: form.symbol.toUpperCase(),
      entry, sl, t1, t2,
      qty: parseInt(form.qty),
      setup: form.setup,
      added: new Date().toISOString().slice(0, 10),
      filters: { ...form.filters },
    };
    setWatchlist([...watchlist, newItem]);
    setForm({
      symbol: "", entry: "", sl: "", t1: "", t2: "", qty: "", setup: "Flat base breakout",
      filters: {
        volume_spike: false, rsi_healthy: false, adx_strong: false,
        sector_strength: false, no_earnings: false, base_quality: false, market_bullish: false,
      },
    });
    setShowForm(false);
  };

  const removeFromWatchlist = (id) =>
    setWatchlist(watchlist.filter(w => w.id !== id));

  const enterTrade = (item) => {
    const newTrade = {
      ...item,
      id: Date.now(),
      entry_date: new Date().toISOString().slice(0, 10),
      status: "OPEN",
    };
    setOpenTrades([...openTrades, newTrade]);
    removeFromWatchlist(item.id);
    alert(`✓ Paper trade opened: ${item.symbol} @ ₹${item.entry}`);
  };

  const closeTrade = (trade, exitPrice, reason) => {
    const closed = {
      ...trade,
      exit: parseFloat(exitPrice),
      exit_date: new Date().toISOString().slice(0, 10),
      reason,
      status: "CLOSED",
    };
    setClosedTrades([...closedTrades, closed]);
    setOpenTrades(openTrades.filter(t => t.id !== trade.id));
  };

  // === METRICS ===
  const m = useMemo(() => {
    const c = closedTrades;
    const wins = c.filter(t => t.exit > t.entry);
    const losses = c.filter(t => t.exit <= t.entry);
    const pnl = (t) => (t.exit - t.entry) * t.qty;
    const totalPnL = c.reduce((s, t) => s + pnl(t), 0);
    const grossWin = wins.reduce((s, t) => s + pnl(t), 0);
    const grossLoss = Math.abs(losses.reduce((s, t) => s + pnl(t), 0));
    const winRate = c.length ? (wins.length / c.length) * 100 : 0;
    const avgWin = wins.length ? grossWin / wins.length : 0;
    const avgLoss = losses.length ? grossLoss / losses.length : 0;
    const profitFactor = grossLoss ? grossWin / grossLoss : 0;
    const expectancy = ((winRate / 100) * avgWin) - ((1 - winRate / 100) * avgLoss);
    return { wins, losses, totalPnL, winRate, avgWin, avgLoss, profitFactor, expectancy };
  }, [closedTrades]);

  const equityCurve = useMemo(() => {
    let running = 0;
    return [...closedTrades]
      .sort((a, b) => new Date(a.exit_date) - new Date(b.exit_date))
      .map(t => {
        running += (t.exit - t.entry) * t.qty;
        return { date: t.exit_date.slice(5), pnl: running, symbol: t.symbol };
      });
  }, [closedTrades]);

  const filterAnalysis = useMemo(() => {
    return Object.keys(FILTER_LABELS).map(filter => {
      const winsPass = m.wins.filter(t => t.filters?.[filter]).length;
      const lossesPass = m.losses.filter(t => t.filters?.[filter]).length;
      return {
        filter: FILTER_LABELS[filter],
        key: filter,
        winsPassPct: m.wins.length ? (winsPass / m.wins.length) * 100 : 0,
        lossesPassPct: m.losses.length ? (lossesPass / m.losses.length) * 100 : 0,
      };
    });
  }, [m]);

  // === STYLES ===
  const C = {
    bg: "#0A0A0F", card: "#111120", border: "#1E2035",
    text: "#E2E8F0", subtle: "#64748B", accent: "#6366F1",
    green: "#10B981", red: "#EF4444", amber: "#F59E0B",
  };

  const s = {
    container: { minHeight: "100vh", background: C.bg, color: C.text,
      fontFamily: "'DM Mono', 'Courier New', monospace" },
    header: { background: "linear-gradient(135deg, #0F0F1A, #1A1A2E)",
      borderBottom: `1px solid ${C.border}`, padding: "20px 28px" },
    tab: (active) => ({
      padding: "10px 18px", background: active ? "#151525" : "transparent",
      border: "none",
      borderBottom: active ? `2px solid ${C.accent}` : "2px solid transparent",
      color: active ? "#F1F5F9" : C.subtle,
      fontSize: "12px", letterSpacing: "1.5px", cursor: "pointer",
      fontFamily: "inherit", transition: "all 0.2s",
    }),
    card: { background: C.card, border: `1px solid ${C.border}`,
      borderRadius: "8px", padding: "18px" },
    btn: (color = C.accent, ghost = false) => ({
      padding: "8px 14px", borderRadius: "6px",
      background: ghost ? "transparent" : `${color}22`,
      border: `1px solid ${color}66`, color,
      cursor: "pointer", fontSize: "11px", letterSpacing: "1px",
      fontFamily: "inherit", transition: "all 0.2s",
    }),
    input: { background: "#08080F", border: `1px solid ${C.border}`,
      color: C.text, padding: "8px 12px", borderRadius: "6px",
      fontSize: "12px", fontFamily: "inherit", width: "100%",
      outline: "none" },
    th: { padding: "10px 12px", textAlign: "left", fontSize: "10px",
      letterSpacing: "1.5px", color: C.subtle,
      borderBottom: `1px solid ${C.border}`, textTransform: "uppercase" },
    td: { padding: "12px", fontSize: "12px", color: "#CBD5E1",
      borderBottom: "1px solid #151525" },
    label: { fontSize: "10px", letterSpacing: "1.5px",
      color: C.subtle, textTransform: "uppercase", marginBottom: "4px",
      display: "block" },
  };

  const fmtINR = (n) => `₹${Math.round(n).toLocaleString("en-IN")}`;
  const fmtPct = (n) => `${n.toFixed(1)}%`;

  const passingFilterCount = (filters) =>
    Object.values(filters).filter(Boolean).length;

  const getReadyStatus = (filters) => {
    const passing = passingFilterCount(filters);
    const total = Object.keys(filters).length;
    if (passing === total) return { label: "READY", color: C.green };
    if (passing >= 5) return { label: "ALMOST", color: C.amber };
    return { label: "WAIT", color: C.red };
  };

  return (
    <div style={s.container}>
      {/* === HEADER === */}
      <div style={s.header}>
        <div style={{ display: "flex", justifyContent: "space-between",
          alignItems: "center", flexWrap: "wrap", gap: "12px" }}>
          <div>
            <div style={{ fontSize: "10px", letterSpacing: "3px",
              color: C.accent, marginBottom: "4px" }}>
              NSE/BSE • PAPER TRADING SYSTEM
            </div>
            <h1 style={{ margin: 0, fontSize: "20px", color: "#F1F5F9", fontWeight: "700" }}>
              Watchlist → Trade → Performance
            </h1>
          </div>
          <div style={{ display: "flex", gap: "8px" }}>
            <div style={s.card}>
              <div style={s.label}>Watchlist</div>
              <div style={{ fontSize: "18px", color: C.accent, fontWeight: "700" }}>
                {watchlist.length}
              </div>
            </div>
            <div style={s.card}>
              <div style={s.label}>Open</div>
              <div style={{ fontSize: "18px", color: C.amber, fontWeight: "700" }}>
                {openTrades.length}
              </div>
            </div>
            <div style={s.card}>
              <div style={s.label}>Closed</div>
              <div style={{ fontSize: "18px", color: C.text, fontWeight: "700" }}>
                {closedTrades.length}
              </div>
            </div>
          </div>
        </div>

        <div style={{ display: "flex", gap: 0, marginTop: "16px",
          borderTop: `1px solid ${C.border}`, paddingTop: "8px" }}>
          {[
            { id: "watchlist", label: "📋 WATCHLIST" },
            { id: "open", label: "🟢 OPEN TRADES" },
            { id: "overview", label: "📊 OVERVIEW" },
            { id: "filters", label: "🔍 FILTER EDGE" },
            { id: "postmortem", label: "💀 POST-MORTEM" },
          ].map(t => (
            <button key={t.id} onClick={() => setView(t.id)} style={s.tab(view === t.id)}>
              {t.label}
            </button>
          ))}
        </div>
      </div>

      <div style={{ padding: "24px 28px" }}>

        {/* === WATCHLIST === */}
        {view === "watchlist" && (
          <>
            <div style={{ display: "flex", justifyContent: "space-between",
              marginBottom: "16px", alignItems: "center" }}>
              <div style={{ fontSize: "11px", letterSpacing: "2px", color: "#94A3B8" }}>
                CANDIDATES READY FOR ENTRY
              </div>
              <button onClick={() => setShowForm(!showForm)} style={s.btn(C.accent)}>
                {showForm ? "× CANCEL" : "+ ADD STOCK"}
              </button>
            </div>

            {/* Add form */}
            {showForm && (
              <div style={{ ...s.card, marginBottom: "16px",
                borderColor: `${C.accent}66` }}>
                <div style={{ fontSize: "11px", letterSpacing: "2px",
                  color: C.accent, marginBottom: "14px" }}>
                  + NEW WATCHLIST ENTRY
                </div>

                <div style={{ display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))",
                  gap: "10px", marginBottom: "14px" }}>
                  <div>
                    <label style={s.label}>Symbol *</label>
                    <input style={s.input} placeholder="RELIANCE"
                      value={form.symbol}
                      onChange={e => setForm({...form, symbol: e.target.value})} />
                  </div>
                  <div>
                    <label style={s.label}>Setup</label>
                    <select style={s.input} value={form.setup}
                      onChange={e => setForm({...form, setup: e.target.value})}>
                      <option>Flat base breakout</option>
                      <option>Bull flag</option>
                      <option>EMA crossover</option>
                      <option>RSI pullback</option>
                      <option>Breakout</option>
                    </select>
                  </div>
                  <div>
                    <label style={s.label}>Entry ₹ *</label>
                    <input style={s.input} type="number" placeholder="1340"
                      value={form.entry}
                      onChange={e => setForm({...form, entry: e.target.value})} />
                  </div>
                  <div>
                    <label style={s.label}>Stop Loss *</label>
                    <input style={s.input} type="number" placeholder="1310"
                      value={form.sl}
                      onChange={e => setForm({...form, sl: e.target.value})} />
                  </div>
                  <div>
                    <label style={s.label}>Target 1 *</label>
                    <input style={s.input} type="number" placeholder="1395"
                      value={form.t1}
                      onChange={e => setForm({...form, t1: e.target.value})} />
                  </div>
                  <div>
                    <label style={s.label}>Target 2</label>
                    <input style={s.input} type="number" placeholder="auto"
                      value={form.t2}
                      onChange={e => setForm({...form, t2: e.target.value})} />
                  </div>
                  <div>
                    <label style={s.label}>Quantity *</label>
                    <input style={s.input} type="number" placeholder="333"
                      value={form.qty}
                      onChange={e => setForm({...form, qty: e.target.value})} />
                  </div>
                </div>

                {/* R:R indicator */}
                {form.entry && form.sl && form.t1 && (
                  <div style={{ padding: "10px 14px", background: "#08080F",
                    borderRadius: "6px", marginBottom: "14px",
                    display: "flex", justifyContent: "space-between", fontSize: "12px" }}>
                    <span style={{ color: C.subtle }}>
                      Risk/share: <span style={{ color: C.red }}>
                        ₹{(parseFloat(form.entry) - parseFloat(form.sl)).toFixed(2)}
                      </span>
                    </span>
                    <span style={{ color: C.subtle }}>
                      Reward/share: <span style={{ color: C.green }}>
                        ₹{(parseFloat(form.t1) - parseFloat(form.entry)).toFixed(2)}
                      </span>
                    </span>
                    <span style={{ color: C.subtle }}>
                      R:R: <span style={{
                        color: ((parseFloat(form.t1) - parseFloat(form.entry)) /
                                (parseFloat(form.entry) - parseFloat(form.sl))) >= 2
                                ? C.green : C.amber,
                        fontWeight: "700",
                      }}>
                        1:{((parseFloat(form.t1) - parseFloat(form.entry)) /
                            (parseFloat(form.entry) - parseFloat(form.sl))).toFixed(2)}
                      </span>
                    </span>
                  </div>
                )}

                {/* Filter checklist */}
                <div style={{ fontSize: "10px", letterSpacing: "1.5px",
                  color: C.subtle, marginBottom: "8px" }}>
                  CHECKLIST FILTERS — TICK WHAT THE STOCK PASSES
                </div>
                <div style={{ display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
                  gap: "8px", marginBottom: "16px" }}>
                  {Object.entries(FILTER_LABELS).map(([k, label]) => (
                    <label key={k} style={{
                      display: "flex", alignItems: "center", gap: "8px",
                      padding: "8px 12px",
                      background: form.filters[k] ? `${C.green}22` : "#08080F",
                      border: `1px solid ${form.filters[k] ? `${C.green}66` : C.border}`,
                      borderRadius: "4px", cursor: "pointer",
                      fontSize: "11px",
                      color: form.filters[k] ? C.green : C.text,
                    }}>
                      <input type="checkbox" checked={form.filters[k]}
                        onChange={e => setForm({...form,
                          filters: {...form.filters, [k]: e.target.checked}})}
                        style={{ accentColor: C.green }} />
                      {label}
                    </label>
                  ))}
                </div>

                <div style={{ display: "flex", justifyContent: "space-between",
                  alignItems: "center" }}>
                  <div style={{ fontSize: "11px", color: C.subtle }}>
                    Passing: <b style={{ color: C.green }}>
                      {passingFilterCount(form.filters)}
                    </b> / {Object.keys(form.filters).length} filters
                  </div>
                  <button onClick={addToWatchlist} style={{
                    ...s.btn(C.green), padding: "10px 20px",
                    background: `${C.green}33`,
                  }}>
                    ✓ ADD TO WATCHLIST
                  </button>
                </div>
              </div>
            )}

            {/* Watchlist items */}
            {watchlist.length === 0 ? (
              <div style={{ ...s.card, textAlign: "center", color: C.subtle,
                padding: "40px" }}>
                No stocks in watchlist. Click "+ ADD STOCK" to begin.
              </div>
            ) : (
              <div style={{ display: "grid", gap: "10px" }}>
                {watchlist.map(w => {
                  const status = getReadyStatus(w.filters);
                  const risk = w.entry - w.sl;
                  const reward = w.t1 - w.entry;
                  const rr = reward / risk;
                  return (
                    <div key={w.id} style={{ ...s.card,
                      borderColor: status.color + "44" }}>
                      <div style={{ display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center", flexWrap: "wrap", gap: "10px" }}>
                        <div style={{ display: "flex", gap: "16px",
                          alignItems: "center", flexWrap: "wrap" }}>
                          <div>
                            <div style={{ fontSize: "16px", fontWeight: "700",
                              color: "#F1F5F9" }}>{w.symbol}</div>
                            <div style={{ fontSize: "10px", color: C.subtle }}>
                              {w.setup} • added {w.added}
                            </div>
                          </div>
                          <span style={{
                            padding: "4px 10px", borderRadius: "4px",
                            background: status.color + "22",
                            color: status.color, fontSize: "10px",
                            fontWeight: "700", letterSpacing: "1.5px",
                          }}>{status.label}</span>
                        </div>

                        <div style={{ display: "flex", gap: "16px",
                          alignItems: "center", flexWrap: "wrap" }}>
                          <div style={{ textAlign: "right" }}>
                            <div style={{ fontSize: "10px", color: C.subtle }}>R:R</div>
                            <div style={{ fontSize: "14px",
                              color: rr >= 2 ? C.green : C.amber,
                              fontWeight: "700" }}>1:{rr.toFixed(2)}</div>
                          </div>
                          <button onClick={() => enterTrade(w)} style={{
                            ...s.btn(C.green),
                            background: `${C.green}33`, fontWeight: "700",
                          }}>
                            🚀 OPEN TRADE
                          </button>
                          <button onClick={() => removeFromWatchlist(w.id)}
                            style={s.btn(C.red, true)}>
                            🗑
                          </button>
                        </div>
                      </div>

                      <div style={{ display: "grid",
                        gridTemplateColumns: "repeat(auto-fit, minmax(80px, 1fr))",
                        gap: "8px", marginTop: "12px",
                        padding: "10px", background: "#08080F", borderRadius: "6px" }}>
                        <div>
                          <div style={{ fontSize: "9px", color: C.subtle }}>ENTRY</div>
                          <div style={{ fontSize: "12px", color: C.text,
                            fontWeight: "700" }}>{fmtINR(w.entry)}</div>
                        </div>
                        <div>
                          <div style={{ fontSize: "9px", color: C.subtle }}>SL</div>
                          <div style={{ fontSize: "12px", color: C.red,
                            fontWeight: "700" }}>{fmtINR(w.sl)}</div>
                        </div>
                        <div>
                          <div style={{ fontSize: "9px", color: C.subtle }}>T1</div>
                          <div style={{ fontSize: "12px", color: C.green,
                            fontWeight: "700" }}>{fmtINR(w.t1)}</div>
                        </div>
                        <div>
                          <div style={{ fontSize: "9px", color: C.subtle }}>T2</div>
                          <div style={{ fontSize: "12px", color: C.green,
                            fontWeight: "700" }}>{fmtINR(w.t2)}</div>
                        </div>
                        <div>
                          <div style={{ fontSize: "9px", color: C.subtle }}>QTY</div>
                          <div style={{ fontSize: "12px", color: C.text,
                            fontWeight: "700" }}>{w.qty}</div>
                        </div>
                        <div>
                          <div style={{ fontSize: "9px", color: C.subtle }}>RISK</div>
                          <div style={{ fontSize: "12px", color: C.red,
                            fontWeight: "700" }}>{fmtINR(risk * w.qty)}</div>
                        </div>
                      </div>

                      {/* Filter chips */}
                      <div style={{ display: "flex", flexWrap: "wrap",
                        gap: "4px", marginTop: "10px" }}>
                        {Object.entries(w.filters).map(([k, v]) => (
                          <span key={k} style={{
                            padding: "3px 8px", borderRadius: "3px",
                            fontSize: "9px", letterSpacing: "0.5px",
                            background: v ? `${C.green}22` : `${C.red}22`,
                            color: v ? C.green : C.red,
                            border: `1px solid ${v ? C.green : C.red}44`,
                          }}>
                            {v ? "✓" : "✗"} {FILTER_LABELS[k]}
                          </span>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </>
        )}

        {/* === OPEN TRADES === */}
        {view === "open" && (
          <>
            <div style={{ fontSize: "11px", letterSpacing: "2px",
              color: "#94A3B8", marginBottom: "12px" }}>
              ACTIVE PAPER POSITIONS
            </div>
            {openTrades.length === 0 ? (
              <div style={{ ...s.card, textAlign: "center", color: C.subtle,
                padding: "40px" }}>
                No open trades yet. Add stocks to the watchlist and click "Open Trade".
              </div>
            ) : (
              <div style={{ display: "grid", gap: "10px" }}>
                {openTrades.map(t => (
                  <OpenTradeCard key={t.id} trade={t} onClose={closeTrade}
                    styles={s} colors={C} fmtINR={fmtINR} />
                ))}
              </div>
            )}
          </>
        )}

        {/* === OVERVIEW === */}
        {view === "overview" && (
          <Overview m={m} equityCurve={equityCurve} closedTrades={closedTrades}
            selectedTrade={selectedTrade} setSelectedTrade={setSelectedTrade}
            styles={s} colors={C} fmtINR={fmtINR} fmtPct={fmtPct} />
        )}

        {/* === FILTER ANALYSIS === */}
        {view === "filters" && (
          <FilterAnalysis filterAnalysis={filterAnalysis}
            styles={s} colors={C} fmtPct={fmtPct} />
        )}

        {/* === POST-MORTEM === */}
        {view === "postmortem" && (
          <PostMortem losses={m.losses} styles={s} colors={C} fmtINR={fmtINR} fmtPct={fmtPct} />
        )}
      </div>
    </div>
  );
}

// === OPEN TRADE CARD ===
function OpenTradeCard({ trade, onClose, styles, colors, fmtINR }) {
  const [exitPrice, setExitPrice] = useState("");
  const [showExit, setShowExit] = useState(false);

  const handleClose = (reason) => {
    if (!exitPrice) { alert("Enter exit price"); return; }
    onClose(trade, exitPrice, reason);
  };

  return (
    <div style={{ ...styles.card, borderColor: colors.amber + "44" }}>
      <div style={{ display: "flex", justifyContent: "space-between",
        flexWrap: "wrap", gap: "12px" }}>
        <div>
          <div style={{ fontSize: "16px", fontWeight: "700",
            color: "#F1F5F9" }}>{trade.symbol}</div>
          <div style={{ fontSize: "10px", color: colors.subtle }}>
            {trade.setup} • opened {trade.entry_date}
          </div>
        </div>
        <button onClick={() => setShowExit(!showExit)} style={{
          ...styles.btn(colors.amber),
        }}>
          {showExit ? "× CANCEL" : "🏁 CLOSE TRADE"}
        </button>
      </div>

      <div style={{ display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(80px, 1fr))",
        gap: "8px", marginTop: "12px",
        padding: "10px", background: "#08080F", borderRadius: "6px" }}>
        <div>
          <div style={{ fontSize: "9px", color: colors.subtle }}>ENTRY</div>
          <div style={{ fontSize: "12px", color: colors.text,
            fontWeight: "700" }}>{fmtINR(trade.entry)}</div>
        </div>
        <div>
          <div style={{ fontSize: "9px", color: colors.subtle }}>SL</div>
          <div style={{ fontSize: "12px", color: colors.red,
            fontWeight: "700" }}>{fmtINR(trade.sl)}</div>
        </div>
        <div>
          <div style={{ fontSize: "9px", color: colors.subtle }}>T1</div>
          <div style={{ fontSize: "12px", color: colors.green,
            fontWeight: "700" }}>{fmtINR(trade.t1)}</div>
        </div>
        <div>
          <div style={{ fontSize: "9px", color: colors.subtle }}>T2</div>
          <div style={{ fontSize: "12px", color: colors.green,
            fontWeight: "700" }}>{fmtINR(trade.t2)}</div>
        </div>
        <div>
          <div style={{ fontSize: "9px", color: colors.subtle }}>QTY</div>
          <div style={{ fontSize: "12px", color: colors.text,
            fontWeight: "700" }}>{trade.qty}</div>
        </div>
      </div>

      {showExit && (
        <div style={{ marginTop: "12px", padding: "12px",
          background: "#08080F", borderRadius: "6px",
          border: `1px solid ${colors.amber}44` }}>
          <div style={{ fontSize: "10px", color: colors.subtle,
            marginBottom: "8px", letterSpacing: "1.5px" }}>
            ENTER EXIT PRICE & REASON
          </div>
          <input type="number" placeholder="Exit price" value={exitPrice}
            onChange={e => setExitPrice(e.target.value)}
            style={{ ...styles.input, marginBottom: "10px" }} />
          <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
            <button onClick={() => handleClose("T1 hit")}
              style={styles.btn(colors.green)}>🎯 T1 HIT</button>
            <button onClick={() => handleClose("T2 hit")}
              style={styles.btn(colors.green)}>🎯 T2 HIT</button>
            <button onClick={() => handleClose("SL hit")}
              style={styles.btn(colors.red)}>🚨 SL HIT</button>
            <button onClick={() => handleClose("Trailed out")}
              style={styles.btn(colors.amber)}>📉 TRAILED</button>
            <button onClick={() => handleClose("Manual exit")}
              style={styles.btn(colors.subtle)}>✋ MANUAL</button>
          </div>
        </div>
      )}
    </div>
  );
}

// === OVERVIEW ===
function Overview({ m, equityCurve, closedTrades, selectedTrade,
  setSelectedTrade, styles, colors, fmtINR, fmtPct }) {
  return (
    <>
      <div style={{ display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
        gap: "12px", marginBottom: "20px" }}>
        <KPI label="Total P&L" value={fmtINR(m.totalPnL)}
          color={m.totalPnL >= 0 ? colors.green : colors.red} styles={styles} />
        <KPI label="Win Rate" value={fmtPct(m.winRate)} color="#A5B4FC"
          sub={`${m.wins.length}W / ${m.losses.length}L`} styles={styles} />
        <KPI label="Profit Factor" value={m.profitFactor.toFixed(2)}
          color={m.profitFactor > 1.5 ? colors.green :
                 m.profitFactor > 1 ? colors.amber : colors.red}
          sub={m.profitFactor > 1.5 ? "Healthy" :
               m.profitFactor > 1 ? "Marginal" : "Losing"} styles={styles} />
        <KPI label="Expectancy" value={fmtINR(m.expectancy)}
          color={m.expectancy >= 0 ? colors.green : colors.red}
          sub="Per trade" styles={styles} />
      </div>

      <div style={{ ...styles.card, marginBottom: "16px" }}>
        <div style={{ fontSize: "11px", letterSpacing: "2px",
          color: "#94A3B8", marginBottom: "12px" }}>EQUITY CURVE</div>
        <ResponsiveContainer width="100%" height={240}>
          <LineChart data={equityCurve}>
            <CartesianGrid stroke="#1E2035" strokeDasharray="3 3" />
            <XAxis dataKey="date" stroke="#64748B" fontSize={10} />
            <YAxis stroke="#64748B" fontSize={10}
              tickFormatter={v => `${(v/1000).toFixed(0)}k`} />
            <Tooltip contentStyle={{ background: "#111120",
              border: "1px solid #1E2035", borderRadius: "6px", fontSize: "12px" }}
              formatter={(v, n, p) => [fmtINR(v), p.payload.symbol]} />
            <ReferenceLine y={0} stroke="#475569" />
            <Line type="monotone" dataKey="pnl" stroke="#6366F1"
              strokeWidth={2} dot={{ r: 3, fill: "#A5B4FC" }} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div style={styles.card}>
        <div style={{ fontSize: "11px", letterSpacing: "2px",
          color: "#94A3B8", marginBottom: "12px" }}>CLOSED TRADES</div>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", minWidth: "600px" }}>
            <thead>
              <tr>
                <th style={styles.th}>Symbol</th>
                <th style={styles.th}>Setup</th>
                <th style={{...styles.th, textAlign: "right"}}>Entry</th>
                <th style={{...styles.th, textAlign: "right"}}>Exit</th>
                <th style={{...styles.th, textAlign: "right"}}>P&L</th>
                <th style={styles.th}>Result</th>
              </tr>
            </thead>
            <tbody>
              {closedTrades.map(t => {
                const pnl = (t.exit - t.entry) * t.qty;
                const isWin = t.exit > t.entry;
                return (
                  <tr key={t.id} onClick={() => setSelectedTrade(t)}
                    style={{ cursor: "pointer" }}>
                    <td style={{...styles.td, fontWeight: "700",
                      color: "#F1F5F9"}}>{t.symbol}</td>
                    <td style={{...styles.td, color: "#94A3B8"}}>{t.setup}</td>
                    <td style={{...styles.td, textAlign: "right"}}>{fmtINR(t.entry)}</td>
                    <td style={{...styles.td, textAlign: "right"}}>{fmtINR(t.exit)}</td>
                    <td style={{...styles.td, textAlign: "right", fontWeight: "700",
                      color: isWin ? colors.green : colors.red}}>{fmtINR(pnl)}</td>
                    <td style={styles.td}>
                      <span style={{
                        padding: "3px 8px", borderRadius: "4px",
                        fontSize: "10px", letterSpacing: "1px",
                        background: isWin ? `${colors.green}22` : `${colors.red}22`,
                        color: isWin ? colors.green : colors.red,
                      }}>{t.reason}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

function KPI({ label, value, color, sub, styles }) {
  return (
    <div style={styles.card}>
      <div style={{ fontSize: "10px", letterSpacing: "2px",
        color: "#64748B", textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: "22px", fontWeight: "700", color,
        marginTop: "4px" }}>{value}</div>
      {sub && <div style={{ fontSize: "10px", color: "#475569",
        marginTop: "2px" }}>{sub}</div>}
    </div>
  );
}

// === FILTER ANALYSIS ===
function FilterAnalysis({ filterAnalysis, styles, colors, fmtPct }) {
  return (
    <>
      <div style={{ ...styles.card, marginBottom: "16px" }}>
        <div style={{ fontSize: "11px", letterSpacing: "2px",
          color: "#94A3B8", marginBottom: "4px" }}>
          FILTER PASS-RATE: WINS vs LOSSES
        </div>
        <div style={{ fontSize: "11px", color: colors.subtle, marginBottom: "16px" }}>
          Big gap (high in wins, low in losses) = strong edge filter.
          Small or no gap = filter is noise.
        </div>
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={filterAnalysis} layout="vertical"
            margin={{ top: 0, right: 10, bottom: 0, left: 120 }}>
            <CartesianGrid stroke="#1E2035" strokeDasharray="3 3" />
            <XAxis type="number" stroke="#64748B" fontSize={10}
              domain={[0, 100]} unit="%" />
            <YAxis dataKey="filter" type="category" stroke="#64748B"
              fontSize={10} width={110} />
            <Tooltip contentStyle={{ background: "#111120",
              border: "1px solid #1E2035", borderRadius: "6px", fontSize: "12px" }}
              formatter={(v) => `${v.toFixed(0)}%`} />
            <Bar dataKey="winsPassPct" fill={colors.green} name="Wins" />
            <Bar dataKey="lossesPassPct" fill={colors.red} name="Losses" />
          </BarChart>
        </ResponsiveContainer>
        <div style={{ display: "flex", gap: "20px", marginTop: "8px",
          justifyContent: "center", fontSize: "11px" }}>
          <span style={{ color: colors.green }}>■ Wins pass rate</span>
          <span style={{ color: colors.red }}>■ Losses pass rate</span>
        </div>
      </div>

      <div style={styles.card}>
        <div style={{ fontSize: "11px", letterSpacing: "2px",
          color: "#94A3B8", marginBottom: "12px" }}>FILTER EDGE RANKING</div>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              <th style={styles.th}>Filter</th>
              <th style={{...styles.th, textAlign: "right"}}>Wins</th>
              <th style={{...styles.th, textAlign: "right"}}>Losses</th>
              <th style={{...styles.th, textAlign: "right"}}>Gap</th>
              <th style={styles.th}>Verdict</th>
            </tr>
          </thead>
          <tbody>
            {filterAnalysis
              .map(f => ({ ...f, gap: f.winsPassPct - f.lossesPassPct }))
              .sort((a, b) => b.gap - a.gap)
              .map(f => (
                <tr key={f.key}>
                  <td style={styles.td}>{f.filter}</td>
                  <td style={{...styles.td, textAlign: "right",
                    color: colors.green}}>{fmtPct(f.winsPassPct)}</td>
                  <td style={{...styles.td, textAlign: "right",
                    color: colors.red}}>{fmtPct(f.lossesPassPct)}</td>
                  <td style={{...styles.td, textAlign: "right", fontWeight: "700",
                    color: f.gap >= 30 ? colors.green :
                           f.gap >= 10 ? colors.amber : colors.subtle}}>
                    {f.gap >= 0 ? "+" : ""}{f.gap.toFixed(0)}%
                  </td>
                  <td style={styles.td}>
                    <span style={{
                      padding: "3px 8px", borderRadius: "4px", fontSize: "10px",
                      letterSpacing: "1px",
                      background: f.gap >= 30 ? `${colors.green}22` :
                                  f.gap >= 10 ? `${colors.amber}22` : `${colors.subtle}22`,
                      color: f.gap >= 30 ? colors.green :
                             f.gap >= 10 ? colors.amber : "#94A3B8",
                    }}>
                      {f.gap >= 30 ? "STRONG EDGE" :
                       f.gap >= 10 ? "MODERATE" : "WEAK/NEUTRAL"}
                    </span>
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

// === POST-MORTEM ===
function PostMortem({ losses, styles, colors, fmtINR, fmtPct }) {
  if (losses.length === 0) {
    return <div style={{ ...styles.card, textAlign: "center", color: colors.subtle,
      padding: "40px" }}>
      No losing trades yet. 🎉
    </div>;
  }

  const failedFilterCounts = {};
  losses.forEach(t => {
    Object.entries(t.filters || {}).forEach(([k, v]) => {
      if (!v) failedFilterCounts[k] = (failedFilterCounts[k] || 0) + 1;
    });
  });
  const sorted = Object.entries(failedFilterCounts).sort((a, b) => b[1] - a[1]);

  return (
    <>
      <div style={{ ...styles.card, marginBottom: "16px",
        borderColor: `${colors.red}44` }}>
        <div style={{ fontSize: "11px", letterSpacing: "2px",
          color: colors.red, marginBottom: "12px" }}>💀 LOSS POST-MORTEM</div>
        {losses.map(t => {
          const pnl = (t.exit - t.entry) * t.qty;
          const failed = Object.entries(t.filters || {})
            .filter(([_, v]) => !v).map(([k]) => FILTER_LABELS[k]);
          return (
            <div key={t.id} style={{ padding: "14px",
              background: "#08080F", border: "1px solid #1E2035",
              borderRadius: "6px", marginBottom: "10px" }}>
              <div style={{ display: "flex", justifyContent: "space-between",
                marginBottom: "8px" }}>
                <div>
                  <span style={{ fontWeight: "700", fontSize: "14px",
                    color: "#F1F5F9" }}>{t.symbol}</span>
                  <span style={{ marginLeft: "10px", fontSize: "11px",
                    color: colors.subtle }}>
                    {t.setup} • {t.entry_date}
                  </span>
                </div>
                <div style={{ fontWeight: "700", color: colors.red,
                  fontSize: "13px" }}>{fmtINR(pnl)}</div>
              </div>
              {failed.length > 0 ? (
                <div>
                  <div style={{ fontSize: "10px", color: "#94A3B8",
                    marginBottom: "6px" }}>
                    🚩 FAILED FILTERS ({failed.length}):
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
                    {failed.map(f => (
                      <span key={f} style={{
                        padding: "3px 8px", borderRadius: "4px",
                        background: `${colors.red}22`,
                        border: `1px solid ${colors.red}44`,
                        fontSize: "10px", color: colors.red,
                      }}>{f}</span>
                    ))}
                  </div>
                </div>
              ) : (
                <div style={{ fontSize: "11px", color: colors.amber }}>
                  ⚠ All filters passed — random noise or missing filter?
                </div>
              )}
            </div>
          );
        })}
      </div>

      {sorted.length > 0 && (
        <div style={styles.card}>
          <div style={{ fontSize: "11px", letterSpacing: "2px",
            color: "#94A3B8", marginBottom: "12px" }}>📚 KEY LESSONS</div>
          <div style={{ fontSize: "12px", color: "#CBD5E1",
            marginBottom: "12px", lineHeight: "1.6" }}>
            Most common reasons losing trades failed:
          </div>
          {sorted.map(([k, count]) => (
            <div key={k} style={{ marginBottom: "8px",
              padding: "8px 12px", background: "#08080F",
              borderRadius: "4px", borderLeft: `3px solid ${colors.red}`,
              display: "flex", justifyContent: "space-between" }}>
              <span style={{ fontSize: "12px", color: "#E2E8F0" }}>
                {FILTER_LABELS[k]}
              </span>
              <span style={{ fontSize: "11px", color: colors.red, fontWeight: "700" }}>
                {count} of {losses.length} losses ({fmtPct(count/losses.length*100)})
              </span>
            </div>
          ))}
          <div style={{ marginTop: "12px", padding: "12px",
            background: `${colors.amber}11`, borderLeft: `3px solid ${colors.amber}`,
            fontSize: "11px", color: "#FCD34D", lineHeight: "1.6" }}>
            💡 <b>ACTION:</b> Make the top failing filter <b>mandatory</b> for entry.
            Don't take trades where it's missing.
          </div>
        </div>
      )}
    </>
  );
}
