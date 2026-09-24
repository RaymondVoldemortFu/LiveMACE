import { cancelOrder } from '@/lib/api/trading'
import { sendClientMessage } from '@/lib/ws/messages'

export function useTradingActions(wsRef: React.MutableRefObject<WebSocket | null> | undefined) {
  return {
    cancelOrder,
    placeOrder(payload: Record<string, unknown>) {
      sendClientMessage(wsRef?.current ?? null, { type: 'place_order', ...payload })
    },
  }
}
