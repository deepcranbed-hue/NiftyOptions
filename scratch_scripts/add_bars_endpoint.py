with open("backend/main.py", "r") as f:
    code = f.read()

endpoint_code = """
@app.get("/api/fetch-historical-bars")
def api_fetch_historical_bars(session_token: str, interval: str, from_date: str, to_date: str):
    import subprocess
    import json
    import dateutil.parser
    from bar_store import save_bars
    try:
        res = subprocess.run([
            "./scratch_scripts/breeze_env/bin/python",
            "scratch_scripts/fetch_breeze_historical.py",
            session_token,
            interval,
            from_date,
            to_date
        ], capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        if "error" in data:
            raise HTTPException(status_code=400, detail=data["error"])
        
        success_rows = data.get("Success", [])
        if not success_rows:
            return {"status": "success", "count": 0, "message": "No data returned"}
            
        formatted_rows = []
        for r in success_rows:
            # datetime from breeze is 'YYYY-MM-DD HH:MM:SS', assume IST
            dt = dateutil.parser.parse(r["datetime"])
            # Format as ISO 8601 with +05:30 offset
            ts_iso = dt.strftime("%Y-%m-%dT%H:%M:%S+05:30")
            formatted_rows.append((ts_iso, r["open"], r["high"], r["low"], r["close"], r["volume"]))
            
        saved_count = save_bars(formatted_rows, timeframe="1d" if interval == "1day" else "1m")
        return {"status": "success", "count": saved_count}
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"Breeze script failed: {e.stderr}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
"""

target = "@app.get(\"/api/health\")"
code = code.replace(target, endpoint_code + "\n" + target)

with open("backend/main.py", "w") as f:
    f.write(code)

print("done")
