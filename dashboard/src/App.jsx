import { useEffect, useState, useCallback, useRef } from 'react'
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis,
  CartesianGrid, Tooltip, ReferenceLine,
} from 'recharts'

const clean = (v) => (v != null && Math.abs(v) < 0.005 ? 0 : v)
const fmt$ = (v) =>
  v == null ? '—' : clean(v).toLocaleString('en-US', { style: 'currency', currency: 'USD' })
const fmtPct = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${clean(v).toFixed(2)}%`)
const cls = (v) => (v > 0 ? 'pos' : v < 0 ? 'neg' : 'muted')

function useApi(path, intervalMs) {
  const [data, setData] = useState(null)
  const refetch = useCallback(() => {
    fetch(path).then((r) => r.json()).then(setData).catch(() => {})
  }, [path])
  useEffect(() => {
    refetch()
    const id = setInterval(refetch, intervalMs)
    return () => clearInterval(id)
  }, [refetch, intervalMs])
  return [data, refetch]
}

function Modal({ title, onClose, children, danger, wide }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className={`modal ${danger ? 'modal-danger' : ''} ${wide ? 'modal-wide' : ''}`}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="modal-close" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  )
}

function ModeToggle() {
  const [mode, setMode] = useState('paper')
  const [step, setStep] = useState(null)
  const [acknowledged, setAcknowledged] = useState(false)
  const [connected, setConnected] = useState(false)
  const [preflight, setPreflight] = useState(null)
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    fetch('/api/mode').then(r => r.json()).then(d => {
      setMode(d.mode || 'paper')
      setConnected(d.connected || false)
    }).catch(() => {})
  }, [])

  const applyMode = async (m) => {
    setError('')
    const res = await fetch('/api/mode', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: m }),
    })
    if (res.ok) {
      setMode(m)
      setStep(null)
      setAcknowledged(false)
      setPreflight(null)
    } else {
      const d = await res.json().catch(() => ({}))
      setError(d.detail || 'Failed to switch mode')
    }
  }

  const runConnect = async () => {
    setChecking(true); setError('')
    try {
      const res = await fetch('/api/robinhood/connect', { method: 'POST' })
      if (res.ok) {
        const d = await res.json()
        setConnected(d.connected || false)
      } else {
        const d = await res.json().catch(() => ({}))
        setError(d.detail || 'Connection failed')
      }
    } catch { setError('Connection failed — check console') }
    finally { setChecking(false) }
  }

  const runPreflight = async () => {
    setChecking(true); setError('')
    try {
      const res = await fetch('/api/robinhood/preflight', { method: 'POST' })
      const d = await res.json()
      setPreflight(d)
      if (!d.success) setError(d.error || 'Preflight failed')
    } catch { setError('Preflight check failed') }
    finally { setChecking(false) }
  }

  const close = () => { setStep(null); setAcknowledged(false); setPreflight(null); setError('') }

  return (
    <>
      <button
        className={`badge badge-btn ${mode === 'live' ? 'live' : 'paper'}`}
        onClick={() => setStep(mode === 'paper' ? 'live-1' : 'paper')}
        title="Switch trading mode"
      >
        {mode === 'live' ? 'LIVE TRADING' : 'PAPER TRADING'}
      </button>

      {/* Step 1: Connect & understand */}
      {step === 'live-1' && (
        <Modal title="Step 1 of 3 — Connect to Robinhood" onClose={close} danger>
          <p>
            To trade with real money, the app must be connected to your{' '}
            <strong>Robinhood Agentic account</strong> via the MCP.
          </p>
          {connected ? (
            <p className="status-ok">✓ Robinhood MCP is connected</p>
          ) : (
            <>
              <p className="modal-muted">
                Click below to start OAuth. A browser window will open for you to
                log in to Robinhood and authorize this app.
              </p>
              <div className="modal-actions">
                <button onClick={runConnect} disabled={checking}>
                  {checking ? 'Connecting…' : 'Connect to Robinhood'}
                </button>
              </div>
            </>
          )}
          {error && <p className="modal-error">{error}</p>}
          <div className="modal-actions">
            <button className="btn-danger-solid" onClick={() => { setError(''); setStep('live-2') }} disabled={!connected}>
              Next
            </button>
            <button onClick={close}>Cancel</button>
          </div>
        </Modal>
      )}

      {/* Step 2: Preflight verification */}
      {step === 'live-2' && (
        <Modal title="Step 2 of 3 — Verify Connection" onClose={close} danger>
          <p>
            We need to confirm your account is accessible and orders can be sent.
            This will query your account info and fetch a live quote — no orders
            will be placed.
          </p>
          {preflight?.success ? (
            <div className="preflight-result">
              <p className="status-ok">✓ Preflight passed</p>
              <p className="modal-muted">
                Account: {preflight.account_number} · Buying power: ${preflight.buying_power} · SPY: ${preflight.spy_price}
              </p>
            </div>
          ) : (
            <div className="modal-actions">
              <button onClick={runPreflight} disabled={checking}>
                {checking ? 'Checking…' : 'Run Preflight Check'}
              </button>
            </div>
          )}
          {error && <p className="modal-error">{error}</p>}
          <div className="modal-actions">
            <button className="btn-danger-solid" onClick={() => { setError(''); setStep('live-3') }} disabled={!preflight?.success}>
              Next
            </button>
            <button onClick={close}>Cancel</button>
          </div>
        </Modal>
      )}

      {/* Step 3: Final confirmation */}
      {step === 'live-3' && (
        <Modal title="Step 3 of 3 — Final Confirmation" onClose={close} danger>
          <p>
            <strong>This is your final confirmation.</strong> Once enabled, the engine
            will place real buy and sell orders in your Robinhood Agentic account
            using the same strategy and risk rules as paper mode.
          </p>
          <ul className="modal-list">
            <li>Max position size: 10% of your account</li>
            <li>Max loss per trade: 5% (hard stop)</li>
            <li>All orders are limit orders (never market)</li>
            <li>You can switch back to paper at any time</li>
          </ul>
          <label className="ack-check">
            <input
              type="checkbox"
              checked={acknowledged}
              onChange={(e) => setAcknowledged(e.target.checked)}
            />
            I understand real money will be used and I accept full responsibility
            for all trades placed by this system
          </label>
          {error && <p className="modal-error">{error}</p>}
          <div className="modal-actions">
            <button
              className="btn-danger-solid"
              disabled={!acknowledged}
              onClick={() => applyMode('live')}
            >
              Enable Live Trading
            </button>
            <button onClick={close}>Cancel</button>
          </div>
        </Modal>
      )}

      {/* Switch BACK to paper */}
      {step === 'paper' && (
        <Modal title="Switch back to Paper Trading?" onClose={close}>
          <p>
            The engine will immediately stop placing real orders and return to
            simulated paper trades. Existing open positions in Robinhood will
            NOT be automatically closed — manage those manually if needed.
          </p>
          <div className="modal-actions">
            <button onClick={() => applyMode('paper')}>Switch to Paper</button>
            <button onClick={close}>Cancel</button>
          </div>
        </Modal>
      )}
    </>
  )
}

function SettingsModal({ onClose }) {
  const [accessKeyId, setAccessKeyId] = useState('')
  const [secretAccessKey, setSecretAccessKey] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  const saveKeys = async () => {
    setSaving(true)
    setSaved(false)
    try {
      const res = await fetch('/api/settings/keys', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          aws_access_key_id: accessKeyId,
          aws_secret_access_key: secretAccessKey,
        }),
      })
      if (res.ok) setSaved(true)
    } catch {
      /* ignore */
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal title="Settings" onClose={onClose}>
      <div className="settings-section">
        <h3>AWS Bedrock Keys</h3>
        <label className="field">
          <span>Access Key ID</span>
          <input
            type="text"
            value={accessKeyId}
            autoComplete="off"
            onChange={(e) => setAccessKeyId(e.target.value)}
            placeholder="AKIA…"
          />
        </label>
        <label className="field">
          <span>Secret Access Key</span>
          <input
            type="password"
            value={secretAccessKey}
            autoComplete="off"
            onChange={(e) => setSecretAccessKey(e.target.value)}
            placeholder="••••••••"
          />
        </label>
        <div className="modal-actions">
          <button onClick={saveKeys} disabled={saving || !accessKeyId || !secretAccessKey}>
            {saving ? 'Saving…' : 'Save'}
          </button>
          {saved && <span className="save-ok">Saved</span>}
        </div>
        <p className="modal-muted">
          Keys are also configurable in <code>backend/config.py</code>
        </p>
      </div>
      <div className="settings-section">
        <h3>Robinhood MCP</h3>
        <div className="mcp-status">
          <span className="dot closed" /> Not connected
        </div>
        <p className="modal-muted">
          Connect via your AI platform — see the Setup Guide button in the header.
        </p>
      </div>
    </Modal>
  )
}

function SetupGuideModal({ onClose }) {
  return (
    <Modal title="Robinhood Agentic Trading Setup" onClose={onClose} wide>
      <p>
        Robinhood Agentic Trading exposes your brokerage account to AI agents through
        an MCP (Model Context Protocol) server. Once connected, an agent can check
        quotes, review your portfolio, and place or manage trades on your behalf —
        always within the limits you configure on your Agentic account.
      </p>

      <div className="settings-section">
        <h3>Connect Your AI Agent</h3>
        <p className="modal-muted">Add this MCP server URL to your AI platform:</p>
        <div className="mcp-url">https://agent.robinhood.com/mcp/trading</div>
      </div>

      <div className="settings-section">
        <h3>Platform Instructions</h3>
        <details className="guide-acc">
          <summary>Claude Code</summary>
          <div className="guide-acc-body">
            <p>Run this in your terminal:</p>
            <pre>claude mcp add --transport http robinhood https://agent.robinhood.com/mcp/trading</pre>
            <p>Then restart Claude Code and complete the OAuth login when prompted.</p>
          </div>
        </details>
        <details className="guide-acc">
          <summary>Claude Desktop</summary>
          <div className="guide-acc-body">
            <p>
              Go to Settings → Connectors → Add custom connector, paste the MCP URL,
              and sign in with your Robinhood account when prompted.
            </p>
          </div>
        </details>
        <details className="guide-acc">
          <summary>ChatGPT</summary>
          <div className="guide-acc-body">
            <p>
              Enable Developer Mode in Settings → Connectors, then add a new connector
              with the MCP URL above and authorize your Robinhood account.
            </p>
          </div>
        </details>
        <details className="guide-acc">
          <summary>Cursor</summary>
          <div className="guide-acc-body">
            <p>Add this to <code>~/.cursor/mcp.json</code> (or your project&apos;s <code>.cursor/mcp.json</code>):</p>
            <pre>{`{
  "mcpServers": {
    "robinhood": {
      "url": "https://agent.robinhood.com/mcp/trading"
    }
  }
}`}</pre>
          </div>
        </details>
        <details className="guide-acc">
          <summary>Other MCP clients</summary>
          <div className="guide-acc-body">
            <p>
              Any client that supports remote MCP servers over HTTP with OAuth can
              connect. Point it at the MCP URL above and complete the Robinhood sign-in
              flow when prompted.
            </p>
          </div>
        </details>
      </div>

      <div className="settings-section">
        <h3>Open an Agentic Account</h3>
        <ol className="guide-steps">
          <li>Open the Robinhood app and go to Account → Agentic Trading.</li>
          <li>Read and accept the agentic trading agreement.</li>
          <li>Set your spending limits and permitted asset types.</li>
          <li>Fund the account — agents can only trade with this dedicated balance.</li>
        </ol>
      </div>

      <div className="settings-section risk-note">
        <h3>Risk Disclaimer</h3>
        <p className="modal-muted">
          Trading involves risk, and automated or agent-directed trading can amplify
          it. AI agents can misread markets or act on stale data. You are responsible
          for all activity in your account. Only allocate funds you can afford to
          lose, and review agent activity regularly. Nothing here is investment
          advice.
        </p>
      </div>
    </Modal>
  )
}

function StatTiles({ p }) {
  const ret = p?.total_return_pct
  const stats = p?.stats || {}
  return (
    <div className="tiles">
      <div className="tile">
        <div className="label">Equity</div>
        <div className="value">{fmt$(p?.equity)}</div>
        <div className={`delta ${cls(ret)}`}>{fmtPct(ret)} all-time</div>
      </div>
      <div className="tile">
        <div className="label">Cash</div>
        <div className="value">{fmt$(p?.cash)}</div>
      </div>
      <div className="tile">
        <div className="label">Open positions</div>
        <div className="value">{p ? p.positions.length : '—'}</div>
      </div>
      <div className="tile">
        <div className="label">Closed trades</div>
        <div className="value">{stats.n_trades ?? '—'}</div>
        <div className="delta flat">
          {stats.win_rate != null ? `${stats.win_rate}% win rate` : 'no trades yet'}
        </div>
      </div>
      <div className="tile">
        <div className="label">Realized P&L</div>
        <div className={`value ${cls(stats.total_pnl)}`}>{fmt$(stats.total_pnl ?? 0)}</div>
      </div>
    </div>
  )
}

function EquityTooltip({ active, payload }) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload
  return (
    <div className="chart-tooltip">
      <div className="t">{new Date(d.t * 1000).toLocaleString()}</div>
      <div className="v">{fmt$(d.equity)}</div>
    </div>
  )
}

function EquityChart({ p }) {
  const curve = p?.equity_curve || []
  const start = p?.starting_cash ?? 1000
  const data = curve.map((pt) => ({ ...pt, label: new Date(pt.t * 1000) }))
  return (
    <div className="card">
      <h2>Equity curve</h2>
      <div className="card-sub">
        Paper account value over time · started at {fmt$(start)}
      </div>
      {data.length < 2 ? (
        <div className="empty">
          Not enough data yet — the curve fills in as the engine runs.
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
            <CartesianGrid stroke="var(--gridline)" vertical={false} />
            <XAxis
              dataKey="t"
              tickFormatter={(t) =>
                new Date(t * 1000).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
              }
              stroke="var(--baseline)"
              tick={{ fill: 'var(--text-muted)', fontSize: 12 }}
              tickLine={false}
            />
            <YAxis
              domain={['auto', 'auto']}
              tickFormatter={(v) => `$${Math.round(v)}`}
              stroke="var(--baseline)"
              tick={{ fill: 'var(--text-muted)', fontSize: 12 }}
              tickLine={false}
              width={56}
            />
            <Tooltip content={<EquityTooltip />} cursor={{ stroke: 'var(--text-muted)', strokeWidth: 1 }} />
            <ReferenceLine y={start} stroke="var(--baseline)" strokeDasharray="0" />
            <Line
              type="monotone" dataKey="equity" stroke="var(--series-1)"
              strokeWidth={2} dot={false} activeDot={{ r: 4, stroke: 'var(--surface)', strokeWidth: 2 }}
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}

function CloseButton({ position, onClosed }) {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)

  const doClose = async () => {
    setBusy(true)
    try {
      const res = await fetch(`/api/positions/${position.id}/close`, { method: 'POST' })
      if (res.ok) onClosed()
    } finally {
      setBusy(false)
      setConfirming(false)
    }
  }

  if (confirming) {
    return (
      <span className="close-confirm">
        <button className="btn-danger" onClick={doClose} disabled={busy}>
          {busy ? 'Closing…' : `Sell ${position.symbol}`}
        </button>
        <button onClick={() => setConfirming(false)} disabled={busy}>Cancel</button>
      </span>
    )
  }
  return (
    <button className="btn-close" onClick={() => setConfirming(true)}>Close</button>
  )
}

function Positions({ p, onClosed }) {
  const rows = p?.positions || []
  return (
    <div className="card">
      <h2>Open positions</h2>
      <div className="card-sub">
        Exits: stop hit, target hit, 5 trading days, or manual close — whichever comes first
      </div>
      {rows.length === 0 ? (
        <div className="empty">No open positions.</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Symbol</th><th>Setup</th><th className="num">Shares</th>
              <th className="num">Entry</th><th className="num">Now</th>
              <th className="num">Stop</th><th className="num">Target</th>
              <th className="num">P&L</th><th className="num">Days held</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="sym">{r.symbol}</td>
                <td><span className="setup-tag">{r.setup}</span></td>
                <td className="num">{r.shares}</td>
                <td className="num">{fmt$(r.entry_price)}</td>
                <td className="num">{fmt$(r.current_price)}</td>
                <td className="num muted">{fmt$(r.stop)}</td>
                <td className="num muted">{fmt$(r.target)}</td>
                <td className={`num ${cls(r.unrealized_pnl)}`}>
                  {fmt$(r.unrealized_pnl)} ({fmtPct(r.unrealized_pnl_pct)})
                </td>
                <td className="num">{r.trading_days_held}/5</td>
                <td className="num"><CloseButton position={r} onClosed={onClosed} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function AiChip({ ai }) {
  if (!ai) return null
  return (
    <span className={`ai-chip ${ai.verdict}`} title={ai.reason}>
      AI: {ai.verdict} {ai.confidence}/10
    </span>
  )
}

function Recommendations({ scan, onRescan, scanning }) {
  const recs = scan?.recommendations || []
  const ts = scan?.timestamp
  const news = scan?.news || {}
  return (
    <div className="card">
      <h2>Current recommendations</h2>
      <div className="card-sub">
        {ts
          ? `Last scan ${new Date(ts * 1000).toLocaleString()} · ${scan.scanned} symbols scanned · market regime ${scan.market_ok ? 'OK (SPY above 50-day)' : 'RISK-OFF (SPY below 50-day — no new entries)'}${scan.ai_reviewed ? ' · AI review ✓' : ''}`
          : 'No scan yet.'}
      </div>
      {scan?.ai_market_note && (
        <div className="ai-note">
          <span className="ai-note-label">AI market read</span>
          {scan.ai_market_note}
        </div>
      )}
      <div className="controls">
        <button onClick={onRescan} disabled={scanning}>
          {scanning ? 'Scanning…' : 'Scan now'}
        </button>
        <span className="note">Auto-scans every 10 min during market hours · Claude reviews news when the candidate list changes (max every 30 min)</span>
      </div>
      {recs.length === 0 ? (
        <div className="empty">No setups passing filters right now.</div>
      ) : (
        <div className="recs">
          {recs.map((r) => (
            <div className={`rec ${r.ai?.verdict === 'veto' ? 'vetoed' : ''}`} key={r.symbol}>
              <div className="rec-head">
                <span>
                  <span className="rec-sym">{r.symbol}</span>{' '}
                  <span className="setup-tag">{r.setup}</span>{' '}
                  <span className="setup-tag">{r.asset_type}</span>{' '}
                  <AiChip ai={r.ai} />
                </span>
                <span className="rec-price">{fmt$(r.price)}</span>
              </div>
              <div className="levels">
                <span className="lv"><span className="k">Stop</span>{fmt$(r.stop)} (−{r.stop_pct}%)</span>
                <span className="lv"><span className="k">Target</span>{fmt$(r.target)} (+{r.target_pct}%)</span>
                <span className="lv"><span className="k">Score</span>{r.score}</span>
                <span className="lv"><span className="k">12w mom</span>{fmtPct(r.mom_12w)}</span>
              </div>
              <div className="rationale">{r.rationale}</div>
              {r.ai && (
                <div className={`ai-reason ${r.ai.verdict}`}>{r.ai.reason}</div>
              )}
              {(news[r.symbol] || []).length > 0 && (
                <details className="news">
                  <summary>{news[r.symbol].length} recent headlines</summary>
                  <ul>
                    {news[r.symbol].map((h, i) => (
                      <li key={i}>
                        <span className="muted">
                          {h.age_hours != null ? `${Math.round(h.age_hours)}h ago` : ''}
                        </span>{' '}
                        {h.title} <span className="muted">— {h.publisher}</span>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function TradeHistory({ p }) {
  const rows = (p?.trades || []).slice(0, 50)
  return (
    <div className="card">
      <h2>Trade history</h2>
      <div className="card-sub">Closed paper trades, most recent first</div>
      {rows.length === 0 ? (
        <div className="empty">No closed trades yet.</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Symbol</th><th>Setup</th><th>Exit reason</th>
              <th className="num">Entry</th><th className="num">Exit</th>
              <th className="num">P&L</th><th>Closed</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => (
              <tr key={t.id}>
                <td className="sym">{t.symbol}</td>
                <td><span className="setup-tag">{t.setup}</span></td>
                <td className="muted">{t.exit_reason.replace('_', ' ')}</td>
                <td className="num">{fmt$(t.entry_price)}</td>
                <td className="num">{fmt$(t.exit_price)}</td>
                <td className={`num ${cls(t.pnl)}`}>{fmt$(t.pnl)} ({fmtPct(t.pnl_pct)})</td>
                <td className="muted">{new Date(t.exit_time * 1000).toLocaleDateString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function RefreshCountdown({ status }) {
  const [secondsLeft, setSecondsLeft] = useState(null)

  useEffect(() => {
    if (!status?.last_price_refresh_at || !status?.price_refresh_interval) {
      setSecondsLeft(null)
      return
    }
    const tick = () => {
      const elapsed = Date.now() / 1000 - status.last_price_refresh_at
      const left = Math.max(0, Math.round(status.price_refresh_interval - elapsed))
      setSecondsLeft(left)
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [status?.last_price_refresh_at, status?.price_refresh_interval])

  if (secondsLeft === null) return null
  return (
    <span className="refresh-countdown" title="Time until next price refresh">
      {secondsLeft === 0 ? 'Refreshing…' : `${secondsLeft}s`}
    </span>
  )
}

function ConsoleDrawer({ open, onClose }) {
  const [logs, setLogs] = useState([])
  const [autoscroll, setAutoscroll] = useState(true)
  const lastId = useRef(0)
  const bodyRef = useRef(null)

  useEffect(() => {
    if (!open) return
    let alive = true
    const poll = async () => {
      try {
        const r = await fetch(`/api/logs?after=${lastId.current}`)
        const d = await r.json()
        if (alive && d.logs?.length) {
          lastId.current = d.logs[d.logs.length - 1].id
          setLogs((prev) => [...prev, ...d.logs].slice(-1000))
        }
      } catch { /* ignore */ }
    }
    poll()
    const iv = setInterval(poll, 1500)
    return () => { alive = false; clearInterval(iv) }
  }, [open])

  useEffect(() => {
    if (autoscroll && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight
    }
  }, [logs, autoscroll])

  if (!open) return null

  const levelClass = (lvl) =>
    lvl === 'ERROR' || lvl === 'CRITICAL' ? 'log-error'
      : lvl === 'WARNING' ? 'log-warn' : 'log-info'

  return (
    <div className="console-drawer">
      <div className="console-head">
        <span className="console-title">
          <span className="console-dot" /> Backend Console
          <span className="console-count">{logs.length} lines</span>
        </span>
        <span className="console-actions">
          <label className="console-toggle">
            <input type="checkbox" checked={autoscroll}
              onChange={(e) => setAutoscroll(e.target.checked)} />
            Auto-scroll
          </label>
          <button className="btn-header" onClick={() => setLogs([])}>Clear</button>
          <button className="btn-header" onClick={onClose}>Close ▾</button>
        </span>
      </div>
      <div className="console-body" ref={bodyRef}>
        {logs.length === 0 ? (
          <div className="console-empty">Waiting for backend activity…</div>
        ) : (
          logs.map((l) => (
            <div className={`console-line ${levelClass(l.level)}`} key={l.id}>
              <span className="console-time">
                {new Date(l.t * 1000).toLocaleTimeString('en-US', { hour12: false })}
              </span>
              <span className="console-lvl">{l.level}</span>
              <span className="console-msg">{l.msg}</span>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

export default function App() {
  const [portfolio, refetchPortfolio] = useApi('/api/portfolio', 10_000)
  const [status] = useApi('/api/status', 5_000)
  const [scan, refetchScan] = useApi('/api/scan', 120_000)
  const [scanning, setScanning] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [showGuide, setShowGuide] = useState(false)
  const [showConsole, setShowConsole] = useState(false)

  const runScan = async () => {
    setScanning(true)
    try {
      await fetch('/api/scan/run', { method: 'POST' })
      refetchScan()
    } finally {
      setScanning(false)
    }
  }

  return (
    <div className="app">
      <div className="header">
        <div>
          <h1>Weekly Swing Trader</h1>
          <div className="sub">S&amp;P 500 + top index ETFs · max 5-day holds · risk-managed entries</div>
        </div>
        <div className="header-actions">
          <ModeToggle />
          <span className="badge">
            <span className={`dot ${status?.market_open ? 'open' : 'closed'}`} />
            {status?.market_open ? 'Market open' : 'Market closed'}
          </span>
          <RefreshCountdown status={status} />
          <button className="btn-header" onClick={() => setShowConsole((v) => !v)}>
            {showConsole ? 'Console ▾' : 'Console'}
          </button>
          <button className="btn-header" onClick={() => setShowGuide(true)}>Setup Guide</button>
          <button className="btn-header" onClick={() => setShowSettings(true)}>Settings</button>
        </div>
      </div>

      {showSettings && <SettingsModal onClose={() => setShowSettings(false)} />}
      {showGuide && <SetupGuideModal onClose={() => setShowGuide(false)} />}

      <div className={showConsole ? 'with-console' : ''}>
        <StatTiles p={portfolio} />
        <EquityChart p={portfolio} />
        <Positions p={portfolio} onClosed={refetchPortfolio} />
        <Recommendations scan={scan} onRescan={runScan} scanning={scanning} />
        <TradeHistory p={portfolio} />
      </div>

      <ConsoleDrawer open={showConsole} onClose={() => setShowConsole(false)} />
    </div>
  )
}
