"""
One-time cleanup for data/positions.csv (GitHub issue #2).

Removes structurally corrupt CLOSED rows and repairs what can be repaired:
  1. delete CLOSED rows with Target_1 <= Entry_Price  (fake targets → fake wins)
  2. delete CLOSED rows with Entry_Hit_Date < Entry_Date (impossible ordering)
  3. drop exact duplicate CLOSED rows (same symbol/entry/levels/outcome/close)
  4. collapse plan variants: one CLOSED row per (Symbol, Entry_Date), keeping
     the earliest exit (the first exit event is when the position stopped existing)
  5. relabel CLOSED SL_LOSS rows that exited above entry → TRAILED_EXIT_PROFIT
  6. repair MOSCHIP-style BOUGHT rows: Entry_Hit_Date must not precede Entry_Date

Usage:
  python core/cleanup_positions.py            # dry-run: full report, writes nothing
  python core/cleanup_positions.py --apply    # backup, write, Telegram summary

Stop the Flask server (and any poll cron) before --apply: the poller rewrites
positions.csv every minute during market hours and would clobber this cleanup.
"""
import argparse
import logging
import os
import shutil
import sys
from datetime import datetime

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):  # Windows consoles default to cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("cleanup_positions")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(_ROOT, ".env"))
except ImportError:
    pass

CSV_PATH = os.path.join(_ROOT, "data", "positions.csv")


def send_telegram_alert(msg: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id or token == "your_bot_token_here":
        logger.info("Telegram not configured. Message: \n%s", msg)
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as exc:
        logger.warning("Telegram send failed: %s", exc)


def _save_atomic(df: pd.DataFrame, path: str):
    tmp = f"{path}.tmp"
    df.to_csv(tmp, index=False)
    os.replace(tmp, path)


def _clean_date(v) -> str:
    s = str(v if v is not None else "").strip()
    return "" if s.lower() in ("nan", "none", "nat", "") else s


def _exit_date(row) -> str:
    return _clean_date(row.get("T2_Hit_Date")) or _clean_date(row.get("SL_Hit_Date"))


def _print_rows(title: str, rows: pd.DataFrame):
    print(f"\n── {title} ({len(rows)} rows) " + "─" * max(0, 40 - len(title)))
    if rows.empty:
        print("   (none)")
        return
    cols = ["Symbol", "Entry_Date", "Setup", "Entry_Price", "Target_1",
            "Current_SL", "Outcome", "Closing_Price"]
    print(rows[cols].to_string(index=True))


def main() -> int:
    parser = argparse.ArgumentParser(description="One-time positions.csv cleanup")
    parser.add_argument("--apply", action="store_true",
                        help="write changes (default is dry-run)")
    args = parser.parse_args()

    if not os.path.exists(CSV_PATH):
        logger.error("positions.csv not found at %s", CSV_PATH)
        return 1
    if os.path.exists(f"{CSV_PATH}.tmp"):
        logger.error("positions.csv.tmp exists — another process may be writing. "
                     "Stop the server first.")
        return 1

    df = pd.read_csv(CSV_PATH)
    n_start = len(df)
    status = df["Status"].fillna("").astype(str).str.strip().str.upper()
    closed = status == "CLOSED"
    n_closed_start = int(closed.sum())

    ep = pd.to_numeric(df["Entry_Price"], errors="coerce")
    t1 = pd.to_numeric(df["Target_1"], errors="coerce")
    cp = pd.to_numeric(df["Closing_Price"], errors="coerce")

    # ── 1. fake targets ──────────────────────────────────────────────────
    fake_mask = closed & t1.notna() & ep.notna() & (t1 <= ep)
    _print_rows("Rule 1 — DELETE: fake targets (T1 <= Entry)", df[fake_mask])

    # ── 2. impossible dates on CLOSED rows ───────────────────────────────
    ehd = df["Entry_Hit_Date"].map(_clean_date)
    ed = df["Entry_Date"].map(_clean_date)
    bad_date_mask = closed & ~fake_mask & (ehd != "") & (ed != "") & (ehd < ed)
    _print_rows("Rule 2 — DELETE: Entry_Hit_Date before Entry_Date", df[bad_date_mask])

    df2 = df[~(fake_mask | bad_date_mask)].copy()

    # ── 3. exact duplicates among remaining CLOSED rows ──────────────────
    status2 = df2["Status"].fillna("").astype(str).str.strip().str.upper()
    closed2 = status2 == "CLOSED"
    dup_cols = ["Symbol", "Entry_Date", "Entry_Price", "Target_1", "Target_2",
                "Current_SL", "Outcome", "Closing_Price"]
    dup_mask = closed2 & df2.duplicated(subset=dup_cols, keep="first")
    _print_rows("Rule 3 — DELETE: exact duplicate CLOSED rows", df2[dup_mask])
    df3 = df2[~dup_mask].copy()

    # ── 4. variant collapse: one CLOSED row per (Symbol, Entry_Date) ─────
    status3 = df3["Status"].fillna("").astype(str).str.strip().str.upper()
    closed3 = status3 == "CLOSED"
    closed_rows = df3[closed3]
    drop_idx = []
    for (sym, edate), grp in closed_rows.groupby(["Symbol", "Entry_Date"]):
        if len(grp) <= 1:
            continue
        # keep the earliest exit; blank exit dates sort last
        keep = min(grp.index, key=lambda i: (_exit_date(df3.loc[i]) or "9999-99-99", i))
        drop_idx.extend(i for i in grp.index if i != keep)
    _print_rows("Rule 4 — DELETE: extra plan variants (same Symbol+Entry_Date)",
                df3.loc[drop_idx] if drop_idx else df3.iloc[0:0])
    df4 = df3.drop(index=drop_idx).copy()

    # ── 5. relabel profitable "SL_LOSS" exits ────────────────────────────
    status4 = df4["Status"].fillna("").astype(str).str.strip().str.upper()
    ep4 = pd.to_numeric(df4["Entry_Price"], errors="coerce")
    cp4 = pd.to_numeric(df4["Closing_Price"], errors="coerce")
    relabel_mask = ((status4 == "CLOSED")
                    & (df4["Outcome"].astype(str) == "SL_LOSS")
                    & ep4.notna() & cp4.notna() & (cp4 > ep4))
    _print_rows("Rule 5 — RELABEL → TRAILED_EXIT_PROFIT (SL_LOSS but exit > entry)",
                df4[relabel_mask])
    df4["Outcome"] = df4["Outcome"].astype(object)
    df4.loc[relabel_mask, "Outcome"] = "TRAILED_EXIT_PROFIT"

    # ── 6. repair live BOUGHT rows with impossible Entry_Hit_Date ────────
    status5 = df4["Status"].fillna("").astype(str).str.strip().str.upper()
    ehd4 = df4["Entry_Hit_Date"].map(_clean_date)
    ed4 = df4["Entry_Date"].map(_clean_date)
    repair_mask = (status5 == "BOUGHT") & (ehd4 != "") & (ed4 != "") & (ehd4 < ed4)
    _print_rows("Rule 6 — REPAIR: BOUGHT rows, Entry_Hit_Date reset to Entry_Date",
                df4[repair_mask])
    df4["Entry_Hit_Date"] = df4["Entry_Hit_Date"].astype(object)
    df4.loc[repair_mask, "Entry_Hit_Date"] = df4.loc[repair_mask, "Entry_Date"]
    for _, r in df4[repair_mask].iterrows():
        sl_v = pd.to_numeric(pd.Series([r.get("Current_SL")]), errors="coerce").iloc[0]
        ep_v = pd.to_numeric(pd.Series([r.get("Entry_Price")]), errors="coerce").iloc[0]
        if pd.notna(sl_v) and pd.notna(ep_v) and sl_v > ep_v:
            print(f"   ⚠ REVIEW {r['Symbol']}: live SL {sl_v:.2f} is above entry "
                  f"{ep_v:.2f} — its stop-out will be a TRAILED_EXIT_PROFIT")

    # ── summary ──────────────────────────────────────────────────────────
    statusF = df4["Status"].fillna("").astype(str).str.strip().str.upper()
    closedF = df4[statusF == "CLOSED"]
    epF = pd.to_numeric(closedF["Entry_Price"], errors="coerce")
    cpF = pd.to_numeric(closedF["Closing_Price"], errors="coerce")
    validF = (epF > 0) & (cpF > 0)
    wins = int(((cpF > epF) & validF).sum())
    losses = int(((cpF <= epF) & validF).sum())
    scored = int(validF.sum())
    win_rate = round(100.0 * wins / scored, 1) if scored else 0.0

    n_deleted = n_start - len(df4)
    print("\n" + "=" * 64)
    print(f"Rows:          {n_start} → {len(df4)}  (deleted {n_deleted})")
    print(f"CLOSED trades: {n_closed_start} → {len(closedF)}")
    print(f"Relabeled:     {int(relabel_mask.sum())} → TRAILED_EXIT_PROFIT")
    print(f"Repaired:      {int(repair_mask.sum())} BOUGHT date fixes")
    print(f"TRUE win rate (money-based): {wins}W / {losses}L = {win_rate}%")
    print("Outcome labels after cleanup:")
    print(closedF["Outcome"].fillna("").astype(str).value_counts().to_string())
    print("=" * 64)

    if not args.apply:
        print("\nDRY-RUN — nothing written. Re-run with --apply to commit.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = os.path.join(_ROOT, "data", f"positions_backup_{stamp}.csv")
    shutil.copy2(CSV_PATH, backup)
    logger.info("Backup written: %s", backup)

    _save_atomic(df4.reset_index(drop=True), CSV_PATH)
    logger.info("positions.csv rewritten: %d rows", len(df4))

    send_telegram_alert(
        f"🧹 <b>positions.csv cleanup applied</b>\n"
        f"Deleted {n_deleted} corrupt rows "
        f"({int(fake_mask.sum())} fake-target, {int(bad_date_mask.sum())} bad-date, "
        f"{int(dup_mask.sum()) + len(drop_idx)} duplicate/variant)\n"
        f"Relabeled {int(relabel_mask.sum())} → TRAILED_EXIT_PROFIT\n"
        f"True win rate: <b>{wins}W / {losses}L = {win_rate}%</b> over {scored} trades\n"
        f"Backup: {os.path.basename(backup)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
