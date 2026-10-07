type WSHandler = (data: any) => void

const WS_BASE = 'ws://127.0.0.1:8000'

// The gateway refuses (close code 4401) a connection whose first message isn't
// a valid login, and closes an open one when the login expires. The token goes
// in that first message, never in the URL: URLs end up in server logs.
const CLOSE_UNAUTHORIZED = 4401

// The gateway only broadcasts a channel to connections that sent
// {"type":"subscribe","channels":[...]}. Registering a local handler is not
// enough — the client must tell the server which channels it wants.
class WebSocketClient {
  private ws: WebSocket | null = null
  private token: string | null = null
  private handlers: Map<string, Set<WSHandler>> = new Map()
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private shouldReconnect = false

  // Called from several places on page load (store init, App's effect — twice
  // under StrictMode): the same token while a socket is open or opening is a no-op.
  connect(token: string) {
    const live = this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)
    if (live && token === this.token) return
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
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    // Only one socket at a time: a replaced one is closed, and its late events are
    // ignored below — otherwise its onclose would null out (orphan) the new socket.
    if (this.ws) {
      const old = this.ws
      this.ws = null
      old.close()
    }
    try {
      const ws = new WebSocket(`${WS_BASE}/ws`)
      this.ws = ws

      ws.onopen = () => {
        if (this.ws !== ws) return
        // Log in first; the server reads messages in order, so subscribing right after is safe.
        ws.send(JSON.stringify({ type: 'auth', token: this.token }))
        // (Re)subscribe to every channel we have handlers for — also after reconnects.
        this._sendSubscribe([...this.handlers.keys()])
      }

      ws.onmessage = (event) => {
        if (this.ws !== ws) return
        try {
          const msg = JSON.parse(event.data)
          const handlers = this.handlers.get(msg.type)
          if (handlers) handlers.forEach((h) => h(msg.data))
        } catch (e) {
          // ignore parse errors
        }
      }

      ws.onclose = (event) => {
        if (this.ws !== ws) return
        this.ws = null
        // A refused or expired login won't get better by retrying with the same
        // token; the next REST call gets a 401 and the app logs out (authStore).
        if (event.code === CLOSE_UNAUTHORIZED) this.shouldReconnect = false
        if (this.shouldReconnect) {
          this.reconnectTimer = setTimeout(() => this._open(), 3000)
        }
      }

      ws.onerror = () => {
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
