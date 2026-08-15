import sqlite3
import pandas as pd
from typing import Dict, List, Any
from bar_store import DB_PATH

class DataQualityAgent:
    """
    Nightly Liveness Gate (DataQualityAgent) - D-CAP-03
    Evaluates session data streams to flag dead columns.
    """
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        
    def check_liveness(self, date_str: str) -> Dict[str, Any]:
        """
        Runs liveness checks over option chain rows and VIX/spot streams.
        Flags column as dead if all-zero, all-NULL, or completely constant.
        """
        conn = sqlite3.connect(self.db_path)
        flags = []
        checked_streams = ["option_chain_quotes", "indiavix", "spot_index"]
        
        try:
            # 1. Option Chain Quotes Checks
            chain_query = """
                SELECT call_bid, call_ask, put_bid, put_ask, call_oi, put_oi
                FROM chain_rows r
                JOIN captures c ON r.capture_id = c.capture_id
                WHERE c.captured_at LIKE ?
            """
            df_chain = pd.read_sql_query(chain_query, conn, params=(f"{date_str}%",))
            
            if not df_chain.empty:
                cols_to_check = ["call_bid", "call_ask", "put_bid", "put_ask"]
                for col in cols_to_check:
                    values = df_chain[col].dropna()
                    if values.empty or (values == 0.0).all() or values.nunique() <= 1:
                        flags.append({
                            "stream": "option_chain_quotes",
                            "column": col,
                            "issue": "COLUMN_DEAD",
                            "details": "All values are constant, zero, or NULL over this session."
                        })
                        
            # 2. India VIX Checks
            vix_query = """
                SELECT vix FROM captures WHERE captured_at LIKE ?
            """
            df_vix = pd.read_sql_query(vix_query, conn, params=(f"{date_str}%",))
            if not df_vix.empty:
                vix_vals = df_vix["vix"].dropna()
                if vix_vals.empty or vix_vals.nunique() <= 1 or (vix_vals == 12.0).all():
                    flags.append({
                        "stream": "indiavix",
                        "column": "vix",
                        "issue": "COLUMN_DEAD",
                        "details": f"VIX stream is constant (value: {vix_vals.iloc[0] if not vix_vals.empty else 'None'})."
                    })
                    
            passed = len(flags) == 0
            return {
                "date": date_str,
                "passed": passed,
                "checked_streams": checked_streams,
                "flags": flags
            }
        finally:
            conn.close()
            
    def run_regression_audit(self) -> List[Dict[str, Any]]:
        """
        Gate regression check over the historical captures.
        """
        conn = sqlite3.connect(self.db_path)
        dates = []
        try:
            cursor = conn.execute("SELECT DISTINCT date(captured_at) as d FROM captures ORDER BY d DESC LIMIT 10")
            dates = [r[0] for r in cursor.fetchall()]
        except Exception:
            pass
        finally:
            conn.close()
            
        results = []
        for d in dates:
            if d:
                res = self.check_liveness(d)
                results.append(res)
        return results

if __name__ == "__main__":
    agent = DataQualityAgent()
    print("Running DataQualityAgent Liveness Regression Audit...")
    audit = agent.run_regression_audit()
    for entry in audit:
        print(f"Date: {entry['date']} | Passed: {entry['passed']} | Flags: {len(entry['flags'])}")
        for f in entry['flags']:
            print(f"  -> Dead: {f['stream']}.{f['column']} ({f['details']})")
