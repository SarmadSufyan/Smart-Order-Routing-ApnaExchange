import React, { useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Login } from './pages/Login'
import { AppShell } from './components/AppShell'
import { Dashboard } from './pages/Dashboard'
import { VenueHealth } from './pages/VenueHealth'
import { RiskManager } from './pages/RiskManager'
import { KillSwitch } from './pages/KillSwitch'
import { Wallet } from './pages/Wallet'
import { HowItWorks } from './pages/HowItWorks'
import { useAuthStore } from './stores/authStore'

export default function App() {
  const restoreSession = useAuthStore((s) => s.restoreSession)

  useEffect(() => {
    restoreSession()
  }, [restoreSession])

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />

        <Route path="/" element={<AppShell />}>
          <Route index               element={<Dashboard />} />
          <Route path="wallet"       element={<Wallet />} />
          <Route path="howitworks"   element={<HowItWorks />} />
          <Route path="venues"       element={<VenueHealth />} />
          <Route path="risk"         element={<RiskManager />} />
          <Route path="killswitch"   element={<KillSwitch />} />
          <Route path="order"        element={<Navigate to="/" replace />} />
        </Route>

        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
