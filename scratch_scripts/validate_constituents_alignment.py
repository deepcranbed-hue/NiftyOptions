# scratch_scripts/validate_constituents_alignment.py
import csv
import sys
import os
import json

def validate():
    # Load paths
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    csv_path = os.path.join(root, "nifty-50-stock-list.csv")
    constituents_path = os.path.join(root, "strategy_framework", "config", "constituents.py")
    sync_path = os.path.join(root, "scratch_scripts", "sync_nifty50_to_now.py")
    map_path = os.path.join(root, "strategy_framework", "config", "breeze_symbol_map.json")
    
    # 1. Load CSV symbols
    csv_symbols = set()
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            sym = row.get("Symbol")
            if sym:
                csv_symbols.add(sym.strip())
                
    print(f"Loaded {len(csv_symbols)} symbols from CSV.")
    
    # 2. Check breeze_symbol_map.json
    with open(map_path) as jf:
        breeze_map = json.load(jf)
    map_symbols = set(breeze_map.keys())
    print(f"Loaded {len(map_symbols)} symbols from breeze_symbol_map.json.")
    
    # 3. Check constituents.py
    sys.path.append(root)
    from strategy_framework.config.constituents import WEIGHTS_PCT
    constituents_symbols = set(WEIGHTS_PCT.keys())
    print(f"Loaded {len(constituents_symbols)} symbols from constituents.py.")
    
    # 4. Check sync_nifty50_to_now.py
    import importlib.util
    spec = importlib.util.spec_from_file_location("sync_script", sync_path)
    sync_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync_module)
    sync_symbols = set(sync_module.NIFTY_50_SYMBOLS)
    # Ignore NIFTY index and INDIAVIX if present in sync list
    sync_symbols.discard("NIFTY")
    sync_symbols.discard("INDIAVIX")
    print(f"Loaded {len(sync_symbols)} stock symbols from sync script.")
    
    # Discrepancy checks
    errors = 0
    
    if csv_symbols != constituents_symbols:
        print(f"\nERROR: CSV and constituents.py mismatch!")
        print(f"Only in CSV: {csv_symbols - constituents_symbols}")
        print(f"Only in constituents.py: {constituents_symbols - csv_symbols}")
        errors += 1
        
    if csv_symbols != map_symbols:
        print(f"\nERROR: CSV and breeze_symbol_map.json mismatch!")
        print(f"Only in CSV: {csv_symbols - map_symbols}")
        print(f"Only in breeze_symbol_map.json: {map_symbols - csv_symbols}")
        errors += 1
        
    if csv_symbols != sync_symbols:
        print(f"\nERROR: CSV and sync script mismatch!")
        print(f"Only in CSV: {csv_symbols - sync_symbols}")
        print(f"Only in sync script: {sync_symbols - csv_symbols}")
        errors += 1
        
    # Check weights sum
    total_w = sum(WEIGHTS_PCT.values())
    if abs(total_w - 100.0) > 0.01:
        print(f"\nERROR: Weights in constituents.py sum to {total_w}%, expected 100%")
        errors += 1
        
    # Check Breeze mappings
    for sym in csv_symbols:
        if sym not in breeze_map:
            print(f"\nERROR: Symbol {sym} missing from breeze_symbol_map.json!")
            errors += 1
            
    if errors == 0:
        print("\nSUCCESS: All constituent configurations are 100% consistent!")
        sys.exit(0)
    else:
        print(f"\nFAILURE: Found {errors} inconsistencies.")
        sys.exit(1)

if __name__ == "__main__":
    validate()
