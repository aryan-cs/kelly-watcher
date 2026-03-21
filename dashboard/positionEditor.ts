import Database from 'better-sqlite3'
import {dbPath} from './paths.js'

export const editablePositionStatuses = ['open', 'waiting', 'win', 'lose', 'exit'] as const
export type PositionManualEditStatus = (typeof editablePositionStatuses)[number]

export interface TradeLogManualEditRow {
  trade_log_id: number
  entry_price: number | null
  shares: number | null
  size_usd: number | null
  status: string | null
  updated_at: number
}

export interface PositionManualEditRow {
  market_id: string
  token_id: string
  side: string
  real_money: number
  entry_price: number | null
  shares: number | null
  size_usd: number | null
  status: string | null
  updated_at: number
}

export interface SavePositionManualEditInput {
  sourceKind: 'trade_log' | 'position'
  sourceTradeLogId: number | null
  marketId: string
  tokenId: string
  side: string
  realMoney: number
  entryPrice: number
  shares: number
  sizeUsd: number
  status: PositionManualEditStatus
}

function ensurePositionEditTables(db: Database.Database): void {
  db.exec(`
    CREATE TABLE IF NOT EXISTS trade_log_manual_edits (
      trade_log_id INTEGER PRIMARY KEY,
      entry_price  REAL,
      shares       REAL,
      size_usd     REAL,
      status       TEXT,
      updated_at   INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS position_manual_edits (
      market_id   TEXT NOT NULL,
      token_id    TEXT NOT NULL DEFAULT '',
      side        TEXT NOT NULL,
      real_money  INTEGER NOT NULL DEFAULT 0,
      entry_price REAL,
      shares      REAL,
      size_usd    REAL,
      status      TEXT,
      updated_at  INTEGER NOT NULL,
      PRIMARY KEY (market_id, token_id, side, real_money)
    );
  `)
}

function normalizeStatus(raw: string): PositionManualEditStatus {
  const normalized = raw.trim().toLowerCase()
  if (editablePositionStatuses.includes(normalized as PositionManualEditStatus)) {
    return normalized as PositionManualEditStatus
  }
  throw new Error(`Unsupported position status: ${raw}`)
}

function assertPositiveNumber(value: number, label: string): number {
  if (!Number.isFinite(value) || value <= 0) {
    throw new Error(`${label} must be a positive number`)
  }
  return Number(value.toFixed(6))
}

export function savePositionManualEdit(input: SavePositionManualEditInput): void {
  const nowTs = Math.floor(Date.now() / 1000)
  const marketId = String(input.marketId || '').trim()
  const tokenId = String(input.tokenId || '').trim()
  const side = String(input.side || '').trim().toLowerCase()
  const realMoney = input.realMoney ? 1 : 0
  const entryPrice = assertPositiveNumber(input.entryPrice, 'Entry')
  const shares = assertPositiveNumber(input.shares, 'Shares')
  const sizeUsd = assertPositiveNumber(input.sizeUsd, 'Total')
  const status = normalizeStatus(input.status)

  if (!marketId) {
    throw new Error('Missing market id for manual position edit')
  }
  if (!side) {
    throw new Error('Missing side for manual position edit')
  }

  const db = new Database(dbPath)
  try {
    ensurePositionEditTables(db)

    const saveTradeLogEdit = db.prepare(`
      INSERT INTO trade_log_manual_edits (
        trade_log_id,
        entry_price,
        shares,
        size_usd,
        status,
        updated_at
      ) VALUES (?, ?, ?, ?, ?, ?)
      ON CONFLICT(trade_log_id) DO UPDATE SET
        entry_price=excluded.entry_price,
        shares=excluded.shares,
        size_usd=excluded.size_usd,
        status=excluded.status,
        updated_at=excluded.updated_at
    `)
    const savePositionEdit = db.prepare(`
      INSERT INTO position_manual_edits (
        market_id,
        token_id,
        side,
        real_money,
        entry_price,
        shares,
        size_usd,
        status,
        updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(market_id, token_id, side, real_money) DO UPDATE SET
        entry_price=excluded.entry_price,
        shares=excluded.shares,
        size_usd=excluded.size_usd,
        status=excluded.status,
        updated_at=excluded.updated_at
    `)

    const transaction = db.transaction(() => {
      if (input.sourceKind === 'position') {
        savePositionEdit.run(
          marketId,
          tokenId,
          side,
          realMoney,
          entryPrice,
          shares,
          sizeUsd,
          status,
          nowTs
        )
      }

      if (input.sourceTradeLogId != null) {
        saveTradeLogEdit.run(
          input.sourceTradeLogId,
          entryPrice,
          shares,
          sizeUsd,
          status,
          nowTs
        )
      }
    })

    transaction()
  } finally {
    db.close()
  }
}
