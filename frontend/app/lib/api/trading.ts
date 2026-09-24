import { apiJson } from './client'

export async function cancelOrder(orderId: number): Promise<unknown> {
  return apiJson(`/orders/cancel/${orderId}`, { method: 'POST' })
}
