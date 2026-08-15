import os
import re

with open("backend/main.py", "r") as f:
    content = f.read()

# Replace the api_upload_chain implementation
new_endpoint = """
import tempfile
import shutil
import json
from backend.quant.nse_csv_loader import load_nse_csv, add_oi_change_pct, window_chain

@app.post("/api/upload-chain")
async def api_upload_chain(
    file: UploadFile = File(...),
    spot: float = Form(...),
    days: float = Form(...),
    payload: str = Form(None)
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
        
        # Load state
        news_state = state_manager.read_state("news_state")
        flows_state = state_manager.read_state("flows_state")
        events_state = state_manager.read_state("events_state")
        macro_state = state_manager.read_state("macro_state")
        cues_state = state_manager.read_state("cues_state")
        
        # Parse payload if provided
        data = json.loads(payload) if payload else {}
        
        res = run_pipeline(
            chain=chain,
            half_life_hours=data.get("half_life_hours", 12.0),
            risk_cfg=data.get("risk_cfg"),
            book=data.get("book"),
            current_drawdown_pct=data.get("current_drawdown_pct", 0.0),
            trade_max_loss_pts=data.get("trade_max_loss_pts"),
            trade_delta=data.get("trade_delta"),
            trade_vega=data.get("trade_vega"),
            override_structure=data.get("override_structure"),
            override_is_premium_sell=data.get("override_is_premium_sell"),
            news_state=news_state,
            flows_state=flows_state,
            events_state=events_state,
            macro_state=macro_state,
            cues_state=cues_state,
            opt_weights=data.get("opt_weights"),
            opt_bias=data.get("opt_bias"),
            opt_max_loss_budget=data.get("opt_max_loss_budget", 0),
            opt_min_pop=data.get("opt_min_pop", 0.0),
            opt_cost_per_leg=data.get("opt_cost_per_leg", 5.0),
            opt_window_pts=data.get("opt_window_pts", 600),
            opt_max_wing=data.get("opt_max_wing", 500),
            opt_allow_undefined=data.get("opt_allow_undefined", False),
            opt_top_n=data.get("opt_top_n", 3),
            opt_allow_bad_rnd=data.get("opt_allow_bad_rnd", False)
        )
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
"""

content = re.sub(r'import tempfile.*?raise HTTPException\(status_code=500, detail=str\(e\)\)', new_endpoint.strip(), content, flags=re.DOTALL)

with open("backend/main.py", "w") as f:
    f.write(content)
