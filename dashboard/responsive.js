export function rowsForHeight(height, reserve = 11, min = 4, max) {
    const available = height - reserve;
    return Math.max(min, max != null ? Math.min(max, available) : available);
}
export function stackPanels(width, threshold = 110) {
    return width < threshold;
}
