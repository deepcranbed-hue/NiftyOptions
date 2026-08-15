import sqlite3
import os

DB_PATH = "/Users/deepak/Library/CloudStorage/GoogleDrive-deepcranbed@gmail.com/My Drive/option_chains.db"

def main():
    if not os.path.exists(DB_PATH):
        print(f"Error: Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Find the latest capture ID
    capture = c.execute("SELECT capture_id, captured_at, spot FROM captures ORDER BY capture_id DESC LIMIT 1").fetchone()
    if not capture:
        return
    cap_id = capture["capture_id"]
    spot = capture["spot"]
    target_expiry = "2026-07-14T06:00:00.000Z"

    # Query strikes within ±400 points of spot (23800 to 24600)
    rows = c.execute("""
        SELECT strike, call_oi, call_oi_chg, call_ltp, put_ltp, put_oi, put_oi_chg 
        FROM chain_rows 
        WHERE capture_id=? AND expiry=? AND strike >= 23800 AND strike <= 24600
        ORDER BY strike ASC
    """, (cap_id, target_expiry)).fetchall()

    print("| Strike | Call LTP | Call OI | Call OI Chg (%) | Put LTP | Put OI | Put OI Chg (%) |")
    print("|--------|----------|---------|-----------------|---------|--------|----------------|")
    for r in rows:
        strike = int(r["strike"])
        call_ltp = f"{r['call_ltp']:.1f}" if r["call_ltp"] is not None else "N/A"
        call_oi = f"{int(r['call_oi']):,}" if r["call_oi"] is not None else "0"
        call_oichg = f"{r['call_oi_chg']:.1f}%" if r["call_oi_chg"] is not None else "0.0%"
        put_ltp = f"{r['put_ltp']:.1f}" if r["put_ltp"] is not None else "N/A"
        put_oi = f"{int(r['put_oi']):,}" if r["put_oi"] is not None else "0"
        put_oichg = f"{r['put_oi_chg']:.1f}%" if r["put_oi_chg"] is not None else "0.0%"
        
        # Highlight ATM strike
        strike_str = f"**{strike}** (ATM)" if abs(strike - spot) < 25 else f"{strike}"
        
        print(f"| {strike_str} | {call_ltp} | {call_oi} | {call_oichg} | {put_ltp} | {put_oi} | {put_oichg} |")

if __name__ == "__main__":
    main()
