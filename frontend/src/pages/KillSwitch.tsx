import React, { useMemo, useState } from 'react'
import { C } from '../theme'
import { Card, CardTitle } from '../components/shared'
import { useDataStore } from '../stores/dataStore'
import { isVenueBlacklisted } from '../adapters'

type Alert = {
  sev: 'Critical' | 'Warning' | 'Info'
  sys: string
  msg: string
  status: 'Active' | 'Resolved'
  time: string
}

export function KillSwitch() {
  const riskStatus = useDataStore((s) => s.riskStatus)
  const venues = useDataStore((s) => s.venues)
  const executionReports = useDataStore((s) => s.executionReports)
  const activateKillSwitch = useDataStore((s) => s.activateKillSwitch)
  const deactivateKillSwitch = useDataStore((s) => s.deactivateKillSwitch)

  const killActive = riskStatus?.kill_switch_active ?? false
  const [step, setStep]     = useState(0)
  const [reason, setReason] = useState('')
  const [busy, setBusy]     = useState(false)

  async function activate() {
    if (step === 0) { setStep(1); return }
    if (!reason) return
    setBusy(true)
    await activateKillSwitch(reason)
    setBusy(false)
    setStep(0)
  }

  async function deactivate() {
    setBusy(true)
    await deactivateKillSwitch()
    setBusy(false)
    setReason('')
    setStep(0)
  }

  // Build live alerts from system state
  const alerts: Alert[] = useMemo(() => {
    const list: Alert[] = []
    if (killActive) {
      list.push({
        sev: 'Critical', sys: 'Risk Manager',
        msg: `Kill switch active — ${riskStatus?.kill_switch_reason || 'no reason'}`,
        status: 'Active',
        time: riskStatus?.kill_switch_activated_at?.slice(11, 19) || '—',
      })
    }
    venues.forEach((v) => {
      if (isVenueBlacklisted(v)) {
        list.push({
          sev: 'Critical', sys: 'Venue Adapter',
          msg: `${v.name} blacklisted — excluded from routing`,
          status: 'Active', time: '—',
        })
      } else if ((v.status || '').toUpperCase() === 'DEGRADED' || v.latency_ms > 80) {
        list.push({
          sev: 'Warning', sys: 'Venue Adapter',
          msg: `${v.name} latency elevated — ${v.latency_ms.toFixed(0)}ms`,
          status: 'Active', time: '—',
        })
      }
    })
    const recentRejects = executionReports.filter((r) => r.exec_type === 'REJECT').slice(0, 3)
    recentRejects.forEach((r) => {
      list.push({
        sev: 'Warning', sys: 'Execution',
        msg: `${r.venue_id} rejected ${r.side} ${r.quantity} ${r.symbol}`,
        status: 'Resolved', time: r.timestamp.slice(11, 19),
      })
    })
    if (riskStatus && riskStatus.exposure_utilization_pct > 80) {
      list.push({
        sev: 'Critical', sys: 'Risk Manager',
        msg: `Exposure limit ${riskStatus.exposure_utilization_pct.toFixed(1)}% utilized`,
        status: 'Active', time: '—',
      })
    } else if (riskStatus && riskStatus.exposure_utilization_pct > 60) {
      list.push({
        sev: 'Warning', sys: 'Risk Manager',
        msg: `Exposure approaching limit (${riskStatus.exposure_utilization_pct.toFixed(1)}%)`,
        status: 'Active', time: '—',
      })
    }
    if (list.length === 0) {
      list.push({
        sev: 'Info', sys: 'System',
        msg: 'All systems operating normally.',
        status: 'Resolved', time: '—',
      })
    }
    return list
  }, [killActive, riskStatus, venues, executionReports])

  const th: React.CSSProperties = { padding: '3px 6px', fontSize: 10, color: C.dim, fontWeight: 400, borderBottom: `1px solid ${C.border}`, textAlign: 'left' }
  const tr: React.CSSProperties = { borderBottom: `1px solid ${C.border}40` }

  const sevIcon = (s: string) =>
    s === 'Critical' ? <span style={{ color: C.red }}>●</span> :
    s === 'Warning'  ? <span style={{ color: C.orange }}>▲</span> :
                       <span style={{ color: C.blue }}>ℹ</span>

  const statusStyle = (s: string): React.CSSProperties => ({
    fontSize: 9, padding: '1px 5px', borderRadius: 2,
    background: s === 'Active' ? '#E24B4A22' : '#4CAF5022',
    color:      s === 'Active' ? C.red       : C.green,
  })

  const blacklisted = venues.filter((v) => isVenueBlacklisted(v))

  return (
    <div>
      <div style={{ fontSize: 15, color: C.text, marginBottom: 14 }}>Kill Switch & Alerts</div>

      {/* BIG KILL SWITCH PANEL */}
      <div style={{
        borderRadius: 8, padding: 40, textAlign: 'center',
        background: killActive ? C.red : '#1A1E24',
        border: killActive ? `1px solid ${C.red}` : `1px solid ${C.border}`,
        marginBottom: 16, minHeight: 200,
        transition: 'background .3s, border .3s',
      }}>
        {killActive ? (
          <>
            <div style={{ fontSize: 28, color: '#fff', fontWeight: 700, animation: 'pocBlink 1s infinite' }}>
              ⚠ KILL SWITCH ACTIVE
            </div>
            <div style={{ color: '#ffffffcc', fontSize: 13, marginTop: 10 }}>
              Reason: {riskStatus?.kill_switch_reason || 'Manual activation'}
            </div>
            <div style={{ color: '#ffffffaa', fontSize: 12, marginTop: 4 }}>
              Activated at: {riskStatus?.kill_switch_activated_at?.replace('T', ' ').slice(0, 19) || '—'}
            </div>
            <div style={{ color: '#ffffffcc', fontSize: 14, marginTop: 16 }}>
              All new orders are being rejected by pre-trade check.
            </div>
            <button onClick={deactivate} disabled={busy} style={{
              marginTop: 20, padding: '10px 28px', borderRadius: 5,
              border: 'none', background: '#fff', color: C.red,
              fontSize: 13, cursor: busy ? 'wait' : 'pointer', fontFamily: 'inherit',
            }}>
              {busy ? 'Deactivating…' : 'Deactivate Kill Switch'}
            </button>
          </>
        ) : (
          <>
            <div style={{ fontSize: 36, marginBottom: 8 }}>🛡</div>
            <div style={{ fontSize: 22, color: C.green, marginBottom: 6 }}>System Operating Normally</div>
            <div style={{ fontSize: 11, color: C.dim }}>
              Kill switch is inactive — orders flow through pre-trade checks.
            </div>

            {step === 0 ? (
              <button onClick={activate} style={{
                marginTop: 20, padding: '12px 32px', borderRadius: 5,
                border: 'none', background: C.red, color: '#fff',
                fontSize: 14, cursor: 'pointer', fontFamily: 'inherit',
              }}>
                Activate Kill Switch
              </button>
            ) : (
              <div style={{ marginTop: 16 }}>
                <div style={{ color: C.orange, fontSize: 13, marginBottom: 8 }}>
                  ⚠ Enter reason to confirm:
                </div>
                <input
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="Reason for activation…"
                  style={{
                    width: '100%', maxWidth: 400, padding: '8px 12px',
                    borderRadius: 4, background: '#131619',
                    border: `1px solid ${C.orange}`, color: C.text,
                    fontSize: 12, fontFamily: 'inherit', outline: 'none',
                  }}
                />
                <div style={{ display: 'flex', gap: 8, justifyContent: 'center', marginTop: 10 }}>
                  <button onClick={activate} disabled={!reason || busy} style={{
                    padding: '8px 20px', borderRadius: 4, border: 'none',
                    background: C.red, color: '#fff', fontSize: 12,
                    cursor: busy ? 'wait' : 'pointer', fontFamily: 'inherit',
                    opacity: reason && !busy ? 1 : 0.4,
                  }}>
                    {busy ? 'Activating…' : 'Confirm Kill Switch'}
                  </button>
                  <button onClick={() => { setStep(0); setReason('') }} style={{
                    padding: '8px 20px', borderRadius: 4,
                    border: `1px solid ${C.border}`, background: '#131619',
                    color: C.muted, fontSize: 12, cursor: 'pointer', fontFamily: 'inherit',
                  }}>
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>

        <Card>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 12 }}>
            <span>🔔</span>
            <CardTitle>SYSTEM ALERTS</CardTitle>
          </div>
          <div style={{ maxHeight: 320, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 4 }}>
            {alerts.map((a, i) => {
              const isActive = a.status === 'Active'
              const bg = isActive
                ? a.sev === 'Critical' ? '#E24B4A10' : '#EF9F2710'
                : '#3B8BD408'
              return (
                <div key={i} style={{
                  display: 'flex', alignItems: 'center', gap: 6,
                  padding: '8px 10px', borderRadius: 4, background: bg, fontSize: 11,
                }}>
                  <span style={{ flexShrink: 0 }}>{sevIcon(a.sev)}</span>
                  <span style={{ color: C.dim, width: 55, flexShrink: 0, fontSize: 10 }}>{a.time}</span>
                  <span style={{ color: C.muted, width: 90, flexShrink: 0, fontSize: 10 }}>{a.sys}</span>
                  <span style={{ flex: 1, color: C.text }}>{a.msg}</span>
                  <span style={statusStyle(a.status)}>{a.status}</span>
                </div>
              )
            })}
          </div>
        </Card>

        <Card>
          <CardTitle>BLACKLISTED VENUES</CardTitle>
          {blacklisted.length === 0 ? (
            <div style={{ fontSize: 11, color: C.dim, textAlign: 'center', padding: '20px 0' }}>
              No venues blacklisted. All venues routable.
            </div>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
              <thead>
                <tr>
                  <th style={th}>Venue</th>
                  <th style={th}>Cloud</th>
                  <th style={{ ...th, textAlign: 'right' }}>Health</th>
                  <th style={{ ...th, textAlign: 'right' }}>Status</th>
                </tr>
              </thead>
              <tbody>
                {blacklisted.map((v) => (
                  <tr key={v.venue_id} style={{ ...tr, background: '#EF9F2708' }}>
                    <td style={{ padding: '7px 6px', color: C.orange }}>{v.name}</td>
                    <td style={{ padding: '7px 6px', color: C.muted }}>{v.cloud}</td>
                    <td style={{ padding: '7px 6px', textAlign: 'right', color: C.muted }}>{v.health_score?.toFixed(0)}%</td>
                    <td style={{ padding: '7px 6px', textAlign: 'right', color: C.red }}>BLACKLISTED</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

      </div>
    </div>
  )
}
