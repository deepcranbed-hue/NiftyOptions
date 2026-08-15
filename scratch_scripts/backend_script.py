import os

with open("backend/main.py", "r") as f:
    content = f.read()

# 1. Add imports
if "UploadFile" not in content:
    content = content.replace(
        "from fastapi import FastAPI, HTTPException",
        "from fastapi import FastAPI, HTTPException, File, UploadFile, Form"
    )

# 2. Add endpoint
endpoint_code = """
import tempfile
import shutil
from backend.quant.nse_csv_loader import load_nse_csv, add_oi_change_pct, window_chain

@app.post("/api/upload-chain")
async def api_upload_chain(
    file: UploadFile = File(...),
    spot: float = Form(...),
    days: float = Form(...)
):
    try:
        # Save uploaded file temporarily
        temp_dir = tempfile.mkdtemp()
        temp_path = os.path.join(temp_dir, file.filename)
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        # Parse and prepare chain
        chain = load_nse_csv(temp_path, spot=spot, days=days)
        chain = add_oi_change_pct(chain)
        chain = window_chain(chain)
        
        # Clean up
        shutil.rmtree(temp_dir)
        
        # Run pipeline
        news_state = state_manager.read_state("news_state")
        flows_state = state_manager.read_state("flows_state")
        events_state = state_manager.read_state("events_state")
        macro_state = state_manager.read_state("macro_state")
        cues_state = state_manager.read_state("cues_state")
        
        # We need to construct a default RiskConfig or use what we have
        res = run_pipeline(
            chain=chain,
            half_life_hours=12.0, # defaults
            risk_cfg=None,
            book=None,
            current_drawdown_pct=0.0,
            news_state=news_state,
            flows_state=flows_state,
            events_state=events_state,
            macro_state=macro_state,
            cues_state=cues_state
        )
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
"""

if "@app.post(\"/api/upload-chain\")" not in content:
    content = content + "\n" + endpoint_code

with open("backend/main.py", "w") as f:
    f.write(content)
