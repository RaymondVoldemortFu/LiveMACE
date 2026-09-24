import type { CryptoPrice, PopularCrypto } from "./generated-types"
import { apiJson } from './client'

export async function getCryptoSymbols(): Promise<string[]> {
  return apiJson('/crypto/symbols')
}

export async function getCryptoPrice(symbol: string): Promise<CryptoPrice> {
  return apiJson(`/crypto/price/${symbol}`)
}

export async function getCryptoMarketStatus(symbol: string) {
  return apiJson(`/crypto/status/${symbol}`)
}

export async function getPopularCryptos(): Promise<PopularCrypto[]> {
  return apiJson('/crypto/popular')
}
