import { apiJson } from './client'

export interface CryptoInfoItem {
  item: string
  value: string
}

export async function getCryptoInfo(symbol: string): Promise<{ success?: boolean; data?: CryptoInfoItem[]; error?: string }> {
  return apiJson(`/ranking/crypto-info/${symbol}`)
}
