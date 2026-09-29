type WSHandler = (data: any) => void

const WS_BASE = 'ws://127.0.0.1:8000'

// The gateway only broadcasts a channel to connections that sent
// {"type":"subscribe","channels":[...]}. Registering a local handler is not
// enough — the client must tell the server which channels it wants.
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

  private _sendSubscribe(channels: string[]) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN && channels.length > 0) {
      this.ws.send(JSON.stringify({ type: 'subscribe', channels }))
    }
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
        // (Re)subscribe to every channel we have handlers for — also after reconnects.
        this._sendSubscribe([...this.handlers.keys()])
      }

      this.ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data)
          const handlers = this.handlers.get(msg.type)
          if (handlers) handlers.forEach((h) => h(msg.data))
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
    const isNewChannel = !this.handlers.has(type)
    if (isNewChannel) this.handlers.set(type, new Set())
    this.handlers.get(type)!.add(handler)
    if (isNewChannel) this._sendSubscribe([type])
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
