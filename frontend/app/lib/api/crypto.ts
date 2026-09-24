import { apiJson } from './client'

export async function getCryptoSymbols(): Promise<any> {
  return apiJson('/crypto/symbols')
}

export async function getCryptoPrice(symbol: string) {
  return apiJson(`/crypto/price/${symbol}`)
}

export async function getCryptoMarketStatus(symbol: string) {
  return apiJson(`/crypto/status/${symbol}`)
}

export async function getPopularCryptos(): Promise<any> {
  return apiJson('/crypto/popular')
}
