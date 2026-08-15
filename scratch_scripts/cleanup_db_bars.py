import sqlite3

def cleanup():
    db_path = "option_chains.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Delete all daily bars so they can be re-downloaded cleanly with the new standardized timestamps
    cursor.execute("DELETE FROM price_bars WHERE timeframe = '1d';")
    conn.commit()
    print("Successfully deleted all daily bars ('1d') to clear duplicates. Please re-download them in the UI.")
    conn.close()

if __name__ == "__main__":
    cleanup()
