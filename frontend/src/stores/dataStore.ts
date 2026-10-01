import { create } from 'zustand'
import {
  api,
  type VenueHealth,
  type NBBO,
  type Order,
  type RiskStatus,
  type ExecutionReport,
  type RoutingStats,
  type WalletSnapshot,
  type PortfolioPnL,
  type RiskLimits,
  type TwapParams,
  type TwapState,
} from '../services/api'
import { wsClient } from '../services/websocket'

// setupWebSocket can run again after a re-login; handlers must be wired once.
let wsWired = false

interface DataState {
  venues: VenueHealth[]
  nbbos: Record<string, NBBO>
  orders: Order[]
  riskStatus: RiskStatus | null
  executionReports: ExecutionReport[]
  routingStats: RoutingStats | null
  wallet: WalletSnapshot | null
  pnl: PortfolioPnL | null
  algos: TwapState[]
  loading: boolean
  lastError: string | null

  fetchAlgos: () => Promise<void>
  // Resolve to [the algo, null] or [null, error message].
  startTwap: (p: TwapParams) => Promise<[TwapState | null, string | null]>
  controlAlgo: (id: string, action: 'pause' | 'resume' | 'cancel') => Promise<string | null>

  fetchWallet: () => Promise<void>
  refreshWallet: () => Promise<void>
  fetchPnl: () => Promise<void>
  // Operator actions below resolve to an error message, or null on success.
  resetBaseline: (venueId?: string) => Promise<string | null>
  updateRiskLimits: (limits: RiskLimits) => Promise<string | null>
  fetchVenues: () => Promise<void>
  fetchAllNBBO: () => Promise<void>
  fetchOrders: () => Promise<void>
  fetchRiskStatus: () => Promise<void>
  fetchExecutionReports: () => Promise<void>
  fetchRoutingStats: () => Promise<void>
  fetchAll: () => Promise<void>
  submitOrder: (symbol: string, side: string, qty: number, orderType?: string, limitPrice?: number) => Promise<Order | null>
  cancelOrder: (orderId: string) => Promise<void>
  blacklistVenue: (venueId: string, reason?: string) => Promise<void>
  unblacklistVenue: (venueId: string) => Promise<void>
  degradeVenue: (venueId: string) => Promise<void>
  recoverVenue: (venueId: string) => Promise<void>
  activateKillSwitch: (reason: string) => Promise<void>
  deactivateKillSwitch: () => Promise<string | null>
  setupWebSocket: () => void
}

export const useDataStore = create<DataState>((set, get) => ({
  venues: [],
  nbbos: {},
  orders: [],
  riskStatus: null,
  executionReports: [],
  routingStats: null,
  wallet: null,
  pnl: null,
  algos: [],
  loading: false,
  lastError: null,

  fetchAlgos: async () => {
    try {
      set({ algos: (await api.getAlgos()).algos })
    } catch (e) {
      set({ lastError: (e as Error).message })
    }
  },

  startTwap: async (p) => {
    try {
      const algo = await api.startTwap(p)
      await get().fetchAlgos()
      return [algo, null]
    } catch (e) {
      return [null, (e as Error).message]
    }
  },

  controlAlgo: async (id, action) => {
    try {
      await api.controlAlgo(id, action)
      await get().fetchAlgos()
      return null
    } catch (e) {
      return (e as Error).message
    }
  },

  fetchPnl: async () => {
    try {
      set({ pnl: await api.getPnl() })
    } catch (e) {
      set({ lastError: (e as Error).message })
    }
  },

  resetBaseline: async (venueId) => {
    try {
      set({ pnl: await api.resetBaseline(venueId) })
      await Promise.all([get().fetchWallet(), get().fetchRiskStatus()])
      return null
    } catch (e) {
      return (e as Error).message
    }
  },

  updateRiskLimits: async (limits) => {
    try {
      await api.updateRiskLimits(limits)
      await get().fetchRiskStatus()
      return null
    } catch (e) {
      return (e as Error).message
    }
  },

  fetchWallet: async () => {
    try {
      set({ wallet: await api.getWallet() })
    } catch (e) {
      set({ lastError: (e as Error).message })
    }
  },

  refreshWallet: async () => {
    try {
      set({ wallet: await api.refreshWallet() })
    } catch (e) {
      set({ lastError: (e as Error).message })
    }
  },

  fetchVenues: async () => {
    try {
      const res = await api.getVenues()
      set({ venues: res.venues })
    } catch (e) {
      set({ lastError: (e as Error).message })
    }
  },

  fetchAllNBBO: async () => {
    try {
      const res = await api.getAllNBBO()
      set({ nbbos: res.nbbo })
    } catch (e) {
      set({ lastError: (e as Error).message })
    }
  },

  fetchOrders: async () => {
    try {
      const res = await api.getOrders()
      set({ orders: res.orders })
    } catch (e) {
      set({ lastError: (e as Error).message })
    }
  },

  fetchRiskStatus: async () => {
    try {
      const res = await api.getRiskStatus()
      set({ riskStatus: res })
    } catch (e) {
      set({ lastError: (e as Error).message })
    }
  },

  fetchExecutionReports: async () => {
    try {
      const res = await api.getExecutionReports()
      set({ executionReports: res.reports })
    } catch (e) {
      set({ lastError: (e as Error).message })
    }
  },

  fetchRoutingStats: async () => {
    try {
      const res = await api.getRoutingStatus()
      set({ routingStats: res })
    } catch (e) {
      set({ lastError: (e as Error).message })
    }
  },

  fetchAll: async () => {
    set({ loading: true })
    await Promise.all([
      get().fetchVenues(),
      get().fetchAllNBBO(),
      get().fetchOrders(),
      get().fetchRiskStatus(),
      get().fetchExecutionReports(),
      get().fetchRoutingStats(),
      get().fetchWallet(),
      get().fetchPnl(),
      get().fetchAlgos(),
    ])
    set({ loading: false })
  },

  submitOrder: async (symbol, side, qty, orderType = 'MARKET', limitPrice) => {
    try {
      const order = await api.submitOrder(symbol, side, qty, orderType, limitPrice)
      await get().fetchOrders()
      await get().fetchExecutionReports()
      await get().fetchRiskStatus()
      return order
    } catch (e) {
      set({ lastError: (e as Error).message })
      return null
    }
  },

  cancelOrder: async (orderId) => {
    try {
      await api.cancelOrder(orderId)
      await get().fetchOrders()
    } catch (e) {
      set({ lastError: (e as Error).message })
    }
  },

  blacklistVenue: async (venueId, reason = 'Manual') => {
    try {
      await api.blacklistVenue(venueId, reason)
      await get().fetchVenues()
    } catch (e) {
      set({ lastError: (e as Error).message })
    }
  },

  unblacklistVenue: async (venueId) => {
    try {
      await api.unblacklistVenue(venueId)
      await get().fetchVenues()
    } catch (e) {
      set({ lastError: (e as Error).message })
    }
  },

  degradeVenue: async (venueId) => {
    try {
      await api.degradeVenue(venueId)
      await get().fetchVenues()
    } catch (e) {
      set({ lastError: (e as Error).message })
    }
  },

  recoverVenue: async (venueId) => {
    try {
      await api.recoverVenue(venueId)
      await get().fetchVenues()
    } catch (e) {
      set({ lastError: (e as Error).message })
    }
  },

  activateKillSwitch: async (reason) => {
    try {
      await api.activateKillSwitch(reason)
      await get().fetchRiskStatus()
    } catch (e) {
      set({ lastError: (e as Error).message })
    }
  },

  deactivateKillSwitch: async () => {
    try {
      await api.deactivateKillSwitch()
      await get().fetchRiskStatus()
      return null
    } catch (e) {
      // e.g. the trading-loss limit still holds the kill switch
      return (e as Error).message
    }
  },

  setupWebSocket: () => {
    if (wsWired) return
    wsWired = true
    wsClient.subscribe('wallet_update', () => {
      get().fetchWallet()
      get().fetchPnl()
    })
    wsClient.subscribe('venue_health', () => {
      get().fetchVenues()
    })
    wsClient.subscribe('order_update', () => {
      get().fetchOrders()
    })
    wsClient.subscribe('kill_switch', () => {
      get().fetchRiskStatus()
      get().fetchAlgos()  // the kill switch pauses running TWAPs
    })
    wsClient.subscribe('algo_update', () => {
      get().fetchAlgos()
    })
    wsClient.subscribe('execution_report', () => {
      get().fetchExecutionReports()
      get().fetchRiskStatus()
      get().fetchPnl()
    })
  },
}))
