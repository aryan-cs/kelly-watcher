import Database from 'better-sqlite3';
import { dbPath } from './paths.js';
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
