export function rowsForHeight(height: number, reserve = 11, min = 4, max?: number): number {
  const available = height - reserve
  return Math.max(min, max != null ? Math.min(max, available) : available)
}

export function stackPanels(width: number, threshold = 110): boolean {
  return width < threshold
}
