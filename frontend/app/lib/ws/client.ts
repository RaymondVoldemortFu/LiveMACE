import { decodeServerMessage, resolveWsUrl, sendClientMessage, type ClientMessage, type ServerMessage } from './messages'

export type ConnectionStatus = 'connecting' | 'open' | 'closed' | 'reconnecting'
type Observer = {
  message: (message: ServerMessage) => void
  status?: (status: ConnectionStatus) => void
  open?: () => void
}

/** Owns the one portfolio connection and decodes each wire message once. */
export class PortfolioSocketClient {
  readonly socketRef: { current: WebSocket | null } = { current: null }
  private observers = new Set<Observer>()
  private stop: (() => void) | null = null

  subscribe(observer: Observer): () => void {
    this.observers.add(observer)
    return () => { this.observers.delete(observer) }
  }

  send(message: ClientMessage) { sendClientMessage(this.socketRef.current, message) }

  connect(observer: Observer): () => void {
    const unsubscribe = this.subscribe(observer)
    if (this.stop) throw new Error('Portfolio connection already owned')
    let disposed = false
    let attempts = 0
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined
    let heartbeatTimer: ReturnType<typeof setInterval> | undefined
    let detach = () => {}
    const status = (value: ConnectionStatus) => this.observers.forEach((item) => item.status?.(value))
    const connect = () => {
      if (disposed) return
      detach()
      clearInterval(heartbeatTimer)
      status(attempts ? 'reconnecting' : 'connecting')
      try {
        const socket = new WebSocket(resolveWsUrl())
        this.socketRef.current = socket
        const active = () => !disposed && this.socketRef.current === socket
        const open = () => {
          if (!active()) return
          attempts = 0
          status('open')
          this.observers.forEach((item) => item.open?.())
          heartbeatTimer = setInterval(() => {
            if (active() && socket.readyState === WebSocket.OPEN) this.send({ type: 'ping' })
          }, 25000)
        }
        const message = (event: MessageEvent) => {
          if (!active()) return
          let decoded: ServerMessage | null
          try { decoded = decodeServerMessage(String(event.data)) } catch { return }
          if (decoded) this.observers.forEach((item) => item.message(decoded!))
        }
        const close = () => {
          if (!active()) return
          clearInterval(heartbeatTimer)
          this.socketRef.current = null
          status('closed')
          attempts += 1
          reconnectTimer = setTimeout(connect, 3000)
        }
        const error = () => { /* close owns reconnection */ }
        socket.addEventListener('open', open)
        socket.addEventListener('message', message)
        socket.addEventListener('close', close)
        socket.addEventListener('error', error)
        detach = () => {
          socket.removeEventListener('open', open)
          socket.removeEventListener('message', message)
          socket.removeEventListener('close', close)
          socket.removeEventListener('error', error)
        }
      } catch {
        status('closed')
        attempts += 1
        reconnectTimer = setTimeout(connect, 5000)
      }
    }
    const stop = () => {
      disposed = true
      clearTimeout(reconnectTimer)
      clearInterval(heartbeatTimer)
      detach()
      const socket = this.socketRef.current
      this.socketRef.current = null
      socket?.close(1000, 'App unmounted')
      unsubscribe()
      this.stop = null
    }
    this.stop = stop
    connect()
    return stop
  }
}

export const portfolioSocket = new PortfolioSocketClient()
