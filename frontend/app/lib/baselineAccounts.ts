/** Account names that map to the Baseline UI category (curves / selectors). */
const BASELINE_NAMES = new Set(['buy_hold', 'grid'])

export function isBaselineAccountName(name: string | undefined | null): boolean {
  return BASELINE_NAMES.has((name || '').trim().toLowerCase())
}
