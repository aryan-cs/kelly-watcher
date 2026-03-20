import Database from 'better-sqlite3';
import { dbPath } from './paths.js';
const BEST_WALLET_DROP_PROTECTION_LIMIT = 5;
const RESOLVED_SHADOW_ENTRY_WHERE = `
real_money=0
AND skipped=0
AND COALESCE(source_action, 'buy')='buy'
AND actual_entry_price IS NOT NULL
AND actual_entry_shares IS NOT NULL
AND actual_entry_size_usd IS NOT NULL
AND shadow_pnl_usd IS NOT NULL
`;
function ensureWalletWatchStateTable(db) {
    db.exec(`
    CREATE TABLE IF NOT EXISTS wallet_watch_state (
      wallet_address           TEXT PRIMARY KEY,
      status                   TEXT NOT NULL DEFAULT 'active',
      status_reason            TEXT,
      dropped_at               INTEGER,
      reactivated_at           INTEGER,
      tracking_started_at      INTEGER NOT NULL DEFAULT 0,
      last_source_ts_at_status INTEGER NOT NULL DEFAULT 0,
      updated_at               INTEGER NOT NULL
    )
  `);
    const columns = new Set(db.prepare('PRAGMA table_info(wallet_watch_state)').all()
        .map((row) => String(row.name)));
    if (!columns.has('tracking_started_at')) {
        db.exec("ALTER TABLE wallet_watch_state ADD COLUMN tracking_started_at INTEGER NOT NULL DEFAULT 0");
    }
}
function protectedBestWallets(db) {
    const rows = db.prepare(`
    SELECT
      LOWER(trader_address) AS trader_address,
      ROUND(SUM(CASE WHEN ${RESOLVED_SHADOW_ENTRY_WHERE} THEN COALESCE(shadow_pnl_usd, 0) ELSE 0 END), 3) AS pnl
    FROM trade_log
    GROUP BY LOWER(trader_address)
    HAVING SUM(CASE WHEN ${RESOLVED_SHADOW_ENTRY_WHERE} THEN 1 ELSE 0 END) > 0
    ORDER BY pnl DESC, trader_address ASC
    LIMIT ${BEST_WALLET_DROP_PROTECTION_LIMIT}
  `).all();
    return new Set(rows
        .map((row) => ({
        wallet: String(row.trader_address || '').trim().toLowerCase(),
        pnl: Number(row.pnl || 0)
    }))
        .filter((row) => row.wallet && row.pnl > 0)
        .map((row) => row.wallet));
}
export function reactivateDroppedWallet(walletAddress) {
    const wallet = walletAddress.trim().toLowerCase();
    if (!wallet) {
        return false;
    }
    const nowTs = Math.floor(Date.now() / 1000);
    const db = new Database(dbPath);
    try {
        ensureWalletWatchStateTable(db);
        db.prepare(`
      INSERT INTO wallet_watch_state (
        wallet_address,
        status,
        status_reason,
        dropped_at,
        reactivated_at,
        tracking_started_at,
        updated_at
      ) VALUES (?, 'active', NULL, NULL, ?, ?, ?)
      ON CONFLICT(wallet_address) DO UPDATE SET
        status='active',
        status_reason=NULL,
        dropped_at=NULL,
        reactivated_at=excluded.reactivated_at,
        tracking_started_at=excluded.tracking_started_at,
        updated_at=excluded.updated_at
    `).run(wallet, nowTs, nowTs, nowTs);
        return true;
    }
    finally {
        db.close();
    }
}
export function dropTrackedWallet(walletAddress, reason = 'manual dashboard drop') {
    const wallet = walletAddress.trim().toLowerCase();
    const normalizedReason = reason.trim() || 'manual dashboard drop';
    if (!wallet) {
        return false;
    }
    const nowTs = Math.floor(Date.now() / 1000);
    const db = new Database(dbPath);
    try {
        ensureWalletWatchStateTable(db);
        if (protectedBestWallets(db).has(wallet)) {
            return false;
        }
        const cursorRow = db
            .prepare('SELECT last_source_ts FROM wallet_cursors WHERE wallet_address=?')
            .get(wallet);
        const lastSourceTs = Number(cursorRow?.last_source_ts || 0);
        db.prepare(`
      INSERT INTO wallet_watch_state (
        wallet_address,
        status,
        status_reason,
        dropped_at,
        last_source_ts_at_status,
        updated_at
      ) VALUES (?, 'dropped', ?, ?, ?, ?)
      ON CONFLICT(wallet_address) DO UPDATE SET
        status='dropped',
        status_reason=excluded.status_reason,
        dropped_at=excluded.dropped_at,
        last_source_ts_at_status=excluded.last_source_ts_at_status,
        updated_at=excluded.updated_at
    `).run(wallet, normalizedReason, nowTs, lastSourceTs, nowTs);
        return true;
    }
    finally {
        db.close();
    }
}
