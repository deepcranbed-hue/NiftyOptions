from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import sqlite3

from bar_store import DB_PATH
from data_agent.quality.data_health import missing_report

router = APIRouter(prefix="/api/data-agent", tags=["data-agent"])

class SyncRequest(BaseModel):
    breeze_session_token: str
    expiry_date: str
    symbol: str = "NIFTY"
    interval: str = "1minute"
    start_date: str = ""
    end_date: str = ""

@router.get("/health")
def data_agent_health(db_path: str = DB_PATH):
    try:
        report = missing_report(db_path)
        return {"success": True, "report": report}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/sync")
def data_agent_sync(req: SyncRequest, db_path: str = DB_PATH):
    try:
        # Import the existing verified unified sync function from backend.main
        from backend.main import api_sync_all_data, SyncAllRequest
        
        # Instantiate request payload
        payload = SyncAllRequest(
            breeze_session_token=req.breeze_session_token,
            expiry_date=req.expiry_date,
            symbol=req.symbol,
            interval=req.interval,
            start_date=req.start_date,
            end_date=req.end_date
        )
        
        # Execute unified downloader
        res = api_sync_all_data(payload)
        
        # Run health check post-sync
        report = missing_report(db_path)
        
        return {
            "success": True,
            "unified_sync": res,
            "report": report
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
