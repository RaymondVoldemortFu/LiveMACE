// TypeScript shim for editor/module resolution.
//
// Some environments (older TS server / mismatched moduleResolution) may fail to
// resolve Chart.js types even though the package exists at runtime.
// This keeps typechecking in this app unblocked without changing tsconfig.

declare module 'chart.js' {
  export const Chart: any
  export const CategoryScale: any
  export const LinearScale: any
  export const PointElement: any
  export const LineElement: any
  export const Title: any
  export const Tooltip: any
  export const Legend: any

  export type ChartOptions<TType = any> = any
}
