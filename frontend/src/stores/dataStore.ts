import { create } from 'zustand'
import {
  api,
  type VenueHealth,
  type NBBO,
  type Order,
  type RiskStatus,
  type ExecutionReport,
  type RoutingStats,
} from '../services/api'
import { wsClient } from '../services/websocket'

interface DataState {
  venues: VenueHealth[]
  nbbos: Record<string, NBBO>
  orders: Order[]
  riskStatus: RiskStatus | null
  executionReports: ExecutionReport[]
  routingStats: RoutingStats | null
  loading: boolean
  lastError: string | null

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
  activateKillSwitch: (reason: string) => Promise<void>
  deactivateKillSwitch: () => Promise<void>
  setupWebSocket: () => void
}

export const useDataStore = create<DataState>((set, get) => ({
  venues: [],
  nbbos: {},
  orders: [],
  riskStatus: null,
  executionReports: [],
  routingStats: null,
  loading: false,
  lastError: null,

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
    } catch (e) {
      set({ lastError: (e as Error).message })
    }
  },

  setupWebSocket: () => {
    wsClient.subscribe('venue_health', () => {
      get().fetchVenues()
    })
    wsClient.subscribe('order_update', () => {
      get().fetchOrders()
    })
    wsClient.subscribe('kill_switch', () => {
      get().fetchRiskStatus()
    })
    wsClient.subscribe('execution_report', () => {
      get().fetchExecutionReports()
      get().fetchRiskStatus()
    })
  },
}))
