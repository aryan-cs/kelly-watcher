import {useEffect, useMemo, useState, type PointerEvent as ReactPointerEvent} from 'react'

export interface ResizableColumnSpec {
  key: string
  resizable?: boolean
}

type ColumnWidths = Record<string, number>

interface PersistedColumnWidths {
  columnKeys: string[]
  widths: ColumnWidths
}

const COLUMN_WIDTHS_STORAGE_VERSION = 2

function storageKeyForTable(tableId: string): string {
  return `kelly-watcher:column-widths:v${COLUMN_WIDTHS_STORAGE_VERSION}:${tableId}`
}

function widthsMatchColumns(widths: ColumnWidths, columns: ResizableColumnSpec[]): boolean {
  const widthKeys = Object.keys(widths)
  if (widthKeys.length !== columns.length) {
    return false
  }

  return columns.every((column) => Number.isFinite(widths[column.key]) && widths[column.key] >= 0)
}

function readPersistedWidths(tableId: string, columns: ResizableColumnSpec[]): ColumnWidths | null {
  if (typeof window === 'undefined') {
    return null
  }

  try {
    const rawValue = window.localStorage.getItem(storageKeyForTable(tableId))
    if (!rawValue) {
      return null
    }

    const parsed = JSON.parse(rawValue) as PersistedColumnWidths
    if (
      !parsed ||
      !Array.isArray(parsed.columnKeys) ||
      parsed.columnKeys.join('|') !== columns.map((column) => column.key).join('|') ||
      typeof parsed.widths !== 'object' ||
      parsed.widths === null ||
      !widthsMatchColumns(parsed.widths, columns)
    ) {
      window.localStorage.removeItem(storageKeyForTable(tableId))
      return null
    }

    return parsed.widths
  } catch {
    window.localStorage.removeItem(storageKeyForTable(tableId))
    return null
  }
}

function measureTableWidths(tableId: string, columns: ResizableColumnSpec[]): ColumnWidths | null {
  const table = document.querySelector<HTMLTableElement>(`[data-resizable-table-id="${tableId}"]`)
  if (!table) {
    return null
  }

  const headerCells = Array.from(table.querySelectorAll<HTMLTableCellElement>('thead th[data-column-key]'))
  if (!headerCells.length) {
    return null
  }

  function horizontalPadding(element: HTMLElement): number {
    const styles = window.getComputedStyle(element)
    return Number.parseFloat(styles.paddingLeft || '0') + Number.parseFloat(styles.paddingRight || '0')
  }

  function contentWidth(element: HTMLElement | null): number {
    if (!element) {
      return 0
    }
    return Math.ceil(element.scrollWidth)
  }

  const widths: ColumnWidths = {}
  for (const [index, column] of columns.entries()) {
    const headerCell = headerCells.find((cell) => cell.dataset.columnKey === column.key)
    if (!headerCell) {
      continue
    }

    const headerLabel = headerCell.querySelector<HTMLElement>('.resize-head__label')
    const headerWidth = contentWidth(headerLabel) + horizontalPadding(headerCell) + 12
    const bodyCells = Array.from(
      table.querySelectorAll<HTMLTableCellElement>(`tbody tr td:nth-child(${index + 1})`)
    )

    let widestCellWidth = 0
    for (const cell of bodyCells) {
      const primaryContent = cell.firstElementChild as HTMLElement | null
      const nestedContent =
        primaryContent && primaryContent.children.length === 1
          ? (primaryContent.firstElementChild as HTMLElement | null)
          : null
      const cellWidth =
        Math.max(contentWidth(primaryContent), contentWidth(nestedContent), contentWidth(cell)) +
        horizontalPadding(cell)
      widestCellWidth = Math.max(widestCellWidth, cellWidth)
    }

    widths[column.key] = Math.max(headerWidth, widestCellWidth)
  }

  return Object.keys(widths).length ? widths : null
}

export function useResizableColumns(tableId: string, columns: ResizableColumnSpec[]) {
  const columnKeySignature = columns.map((column) => column.key).join('|')
  const [widths, setWidths] = useState<ColumnWidths | null>(() => readPersistedWidths(tableId, columns))

  const tableWidth = useMemo(() => {
    if (!widths) {
      return undefined
    }
    return Object.values(widths).reduce((total, width) => total + width, 0)
  }, [widths])

  useEffect(() => {
    const persistedWidths = readPersistedWidths(tableId, columns)
    setWidths((currentWidths) => {
      if (currentWidths && widthsMatchColumns(currentWidths, columns)) {
        return currentWidths
      }
      return persistedWidths
    })
  }, [tableId, columnKeySignature])

  useEffect(() => {
    if (widths) {
      return
    }

    const measuredWidths = measureTableWidths(tableId, columns)
    if (measuredWidths) {
      setWidths(measuredWidths)
    }
  }, [columns, tableId, widths, columnKeySignature])

  useEffect(() => {
    if (typeof window === 'undefined' || !widths) {
      return
    }

    const payload: PersistedColumnWidths = {
      columnKeys: columns.map((column) => column.key),
      widths
    }
    window.localStorage.setItem(storageKeyForTable(tableId), JSON.stringify(payload))
  }, [columns, tableId, widths, columnKeySignature])

  function startResize(column: ResizableColumnSpec, event: ReactPointerEvent<HTMLButtonElement>) {
    if (column.resizable === false) {
      return
    }

    event.preventDefault()
    event.stopPropagation()

    const measuredWidths = widths ?? measureTableWidths(tableId, columns)
    if (!measuredWidths || !Number.isFinite(measuredWidths[column.key])) {
      return
    }

    if (!widths) {
      setWidths(measuredWidths)
    }

    const startX = event.clientX
    const startWidth = measuredWidths[column.key]

    function handlePointerMove(moveEvent: PointerEvent) {
      const nextWidth = Math.max(0, Math.round(startWidth + (moveEvent.clientX - startX)))
      setWidths((currentWidths) => ({
        ...(currentWidths ?? measuredWidths),
        [column.key]: nextWidth
      }))
    }

    function handlePointerUp() {
      window.removeEventListener('pointermove', handlePointerMove)
      window.removeEventListener('pointerup', handlePointerUp)
    }

    window.addEventListener('pointermove', handlePointerMove)
    window.addEventListener('pointerup', handlePointerUp)
  }

  function fitColumnsToViewport() {
    const measuredWidths = widths ?? measureTableWidths(tableId, columns)
    if (!measuredWidths) {
      return
    }

    const table = document.querySelector<HTMLTableElement>(`[data-resizable-table-id="${tableId}"]`)
    const viewport = table?.parentElement as HTMLElement | null
    const targetWidth = viewport?.clientWidth ?? 0
    if (!targetWidth) {
      return
    }

    const totalWidth = Object.values(measuredWidths).reduce((total, width) => total + width, 0)
    if (!totalWidth) {
      return
    }

    const ratio = targetWidth / totalWidth
    let assignedWidth = 0
    const nextWidths: ColumnWidths = {}

    columns.forEach((column, index) => {
      const currentWidth = measuredWidths[column.key] ?? 0
      const nextWidth =
        index === columns.length - 1
          ? Math.max(0, targetWidth - assignedWidth)
          : Math.max(0, Math.round(currentWidth * ratio))
      nextWidths[column.key] = nextWidth
      assignedWidth += nextWidth
    })

    setWidths(nextWidths)
  }

  return {widths, tableWidth, startResize, fitColumnsToViewport}
}
