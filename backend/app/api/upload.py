"""
CSV Upload & Parser - Business Reports, Active Listings, Account Health, Ads, FBA Inventory
Week 1: AUD-1, AUD-2 (S3 storage)
"""
import io
from typing import Literal

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.services.csv_parser import parse_csv, detect_report_type
from app.services.s3_storage import upload_to_s3
from app.core.dependencies import get_current_user

router = APIRouter()

# Supported Amazon report types per MVP
REPORT_TYPES = Literal[
    "business_report",
    "active_listings",
    "account_health",
    "ads",
    "fba_inventory",
]


@router.post("/csv")
async def upload_csv(file: UploadFile = File(...), user: str = Depends(get_current_user)):
    """
    Upload CSV file, parse with Pandas, detect report type, store in S3.
    Supports: Business Reports, Active Listings, Account Health, Ads, FBA Inventory.
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Only CSV files are supported")

    try:
        contents = await file.read()
        df = parse_csv(contents)
        report_type = detect_report_type(df)

        # Store in S3 (when configured)
        s3_key = await upload_to_s3(
            contents,
            filename=file.filename,
            report_type=report_type,
        )

        # Include data preview (first 20 rows) for display
        preview = df.head(20).fillna("").astype(str).to_dict(orient="records")

        return {
            "success": True,
            "filename": file.filename,
            "report_type": report_type,
            "rows": len(df),
            "columns": list(df.columns),
            "preview": preview,
            "s3_key": s3_key,
        }
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Upload failed: {str(e)}")


@router.post("/csv/preview")
async def preview_csv(file: UploadFile = File(...), user: str = Depends(get_current_user)):
    """Preview CSV structure without storing"""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Only CSV files are supported")

    contents = await file.read()
    df = parse_csv(contents)
    report_type = detect_report_type(df)

    return {
        "report_type": report_type,
        "rows": len(df),
        "columns": list(df.columns),
        "preview": df.head(5).to_dict(orient="records"),
    }
