type WSHandler = (data: any) => void

const WS_BASE = 'ws://127.0.0.1:8000'

class WebSocketClient {
  private ws: WebSocket | null = null
  private token: string | null = null
  private handlers: Map<string, Set<WSHandler>> = new Map()
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private shouldReconnect = false

  connect(token: string) {
    this.token = token
    this.shouldReconnect = true
    this._open()
  }

  private _open() {
    if (!this.token) return
    try {
      const url = `${WS_BASE}/ws?token=${encodeURIComponent(this.token)}`
      this.ws = new WebSocket(url)

      this.ws.onopen = () => {
        if (this.reconnectTimer) {
          clearTimeout(this.reconnectTimer)
          this.reconnectTimer = null
        }
      }

      this.ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data)
          const type = msg.type
          const data = msg.data
          const handlers = this.handlers.get(type)
          if (handlers) handlers.forEach((h) => h(data))
        } catch (e) {
          // ignore parse errors
        }
      }

      this.ws.onclose = () => {
        this.ws = null
        if (this.shouldReconnect) {
          this.reconnectTimer = setTimeout(() => this._open(), 3000)
        }
      }

      this.ws.onerror = () => {
        // onclose will fire next; reconnect is handled there
      }
    } catch (e) {
      // silently ignore
    }
  }

  disconnect() {
    this.shouldReconnect = false
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    if (this.ws) {
      this.ws.close()
      this.ws = null
    }
  }

  subscribe(type: string, handler: WSHandler) {
    if (!this.handlers.has(type)) this.handlers.set(type, new Set())
    this.handlers.get(type)!.add(handler)
    return () => this.unsubscribe(type, handler)
  }

  unsubscribe(type: string, handler: WSHandler) {
    const set = this.handlers.get(type)
    if (set) set.delete(handler)
  }

  isConnected(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN
  }
}

export const wsClient = new WebSocketClient()
