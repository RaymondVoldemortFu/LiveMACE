import { apiJson } from './client'
import type { EvalAccountCheckpointsResponse, EvalLeaderboardResponse } from './generated-types'

export async function getEvalLeaderboard(
  intervalSeconds: number = 3600,
  orderBy: 'return' | 'pnl' | 'volatility' = 'pnl',
): Promise<EvalLeaderboardResponse> {
  return apiJson(`/evaluation/checkpoints/leaderboard?interval_seconds=${intervalSeconds}&order_by=${orderBy}`)
}

export async function getEvalAccountCheckpoints(
  accountId: number,
  intervalSeconds: number = 3600,
  limit: number = 10,
): Promise<EvalAccountCheckpointsResponse> {
  return apiJson(`/evaluation/checkpoints/account/${accountId}?interval_seconds=${intervalSeconds}&limit=${limit}`)
}
