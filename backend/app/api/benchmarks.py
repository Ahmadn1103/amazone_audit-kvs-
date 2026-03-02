"""
Real-time Amazon seller industry benchmarks via Perplexity Sonar.
Compares user report data against current market averages.
"""
import json
import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.core.config import settings
from app.core.dependencies import get_current_user

router = APIRouter()

# Prompts per report type — ask Sonar for current benchmarks
BENCHMARK_PROMPTS: dict[str, str] = {
    "ads": (
        "What are the current 2024-2025 Amazon Ads industry benchmarks for sellers? "
        "Return ONLY a valid JSON object with numeric values (no % or $ symbols), using exactly these keys: "
        '{"acos": <avg ACoS as number e.g. 30>, "roas": <avg ROAS as number e.g. 3.5>, '
        '"ctr": <avg CTR % as number e.g. 0.4>, "cpc": <avg CPC in USD as number e.g. 1.20>}'
    ),
    "business_report": (
        "What are the current 2024-2025 Amazon seller business performance benchmarks? "
        "Return ONLY a valid JSON object with numeric values, using exactly these keys: "
        '{"conversion_rate": <avg conversion rate % e.g. 12.5>, '
        '"units_per_order": <avg units per order e.g. 1.3>, '
        '"buy_box_percentage": <avg buy box win rate % e.g. 82>, '
        '"return_rate": <avg return rate % e.g. 5>}'
    ),
    "account_health": (
        "What are the current 2024-2025 Amazon seller account health metrics and benchmarks? "
        "Return ONLY a valid JSON object with numeric values, using exactly these keys: "
        '{"order_defect_rate": <typical seller ODR % e.g. 0.3>, '
        '"late_shipment_rate": <typical late shipment rate % e.g. 1.5>, '
        '"valid_tracking_rate": <typical valid tracking rate % e.g. 98>, '
        '"cancellation_rate": <typical pre-fulfillment cancel rate % e.g. 0.8>}'
    ),
    "fba_inventory": (
        "What are the current 2024-2025 Amazon FBA inventory management benchmarks? "
        "Return ONLY a valid JSON object with numeric values, using exactly these keys: "
        '{"in_stock_rate": <avg in-stock rate % e.g. 95>, '
        '"inventory_turnover": <avg annual inventory turns e.g. 8>, '
        '"stranded_rate": <avg stranded inventory % e.g. 2>, '
        '"aged_inventory_rate": <avg 180+ day aged inventory % e.g. 5>}'
    ),
    "active_listings": (
        "What are the current 2024-2025 Amazon active listing performance benchmarks? "
        "Return ONLY a valid JSON object with numeric values, using exactly these keys: "
        '{"buy_box_percentage": <avg buy box win rate % e.g. 82>, '
        '"listing_quality_score": <avg listing quality out of 100 e.g. 75>, '
        '"image_count": <avg images per listing e.g. 7>, '
        '"review_count": <avg reviews per ASIN e.g. 150>}'
    ),
}

# Human-readable labels and units for chart display
BENCHMARK_META: dict[str, dict[str, dict]] = {
    "ads": {
        "acos":  {"label": "ACoS",  "unit": "%",  "lower_is_better": True},
        "roas":  {"label": "ROAS",  "unit": "x",  "lower_is_better": False},
        "ctr":   {"label": "CTR",   "unit": "%",  "lower_is_better": False},
        "cpc":   {"label": "CPC",   "unit": "$",  "lower_is_better": True},
    },
    "business_report": {
        "conversion_rate":    {"label": "Conv. Rate",   "unit": "%", "lower_is_better": False},
        "units_per_order":    {"label": "Units/Order",  "unit": "x", "lower_is_better": False},
        "buy_box_percentage": {"label": "Buy Box Win",  "unit": "%", "lower_is_better": False},
        "return_rate":        {"label": "Return Rate",  "unit": "%", "lower_is_better": True},
    },
    "account_health": {
        "order_defect_rate":   {"label": "Defect Rate",    "unit": "%", "lower_is_better": True},
        "late_shipment_rate":  {"label": "Late Shipment",  "unit": "%", "lower_is_better": True},
        "valid_tracking_rate": {"label": "Valid Tracking", "unit": "%", "lower_is_better": False},
        "cancellation_rate":   {"label": "Cancel Rate",    "unit": "%", "lower_is_better": True},
    },
    "fba_inventory": {
        "in_stock_rate":       {"label": "In-Stock Rate",  "unit": "%",     "lower_is_better": False},
        "inventory_turnover":  {"label": "Inv. Turnover",  "unit": "x/yr",  "lower_is_better": False},
        "stranded_rate":       {"label": "Stranded Inv.",  "unit": "%",     "lower_is_better": True},
        "aged_inventory_rate": {"label": "Aged Inventory", "unit": "%",     "lower_is_better": True},
    },
    "active_listings": {
        "buy_box_percentage":   {"label": "Buy Box Win",       "unit": "%",    "lower_is_better": False},
        "listing_quality_score":{"label": "Listing Quality",   "unit": "/100", "lower_is_better": False},
        "image_count":          {"label": "Avg Images",        "unit": "",     "lower_is_better": False},
        "review_count":         {"label": "Avg Reviews",       "unit": "",     "lower_is_better": False},
    },
}


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences that LLMs sometimes include."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # Drop first line (```json or ```) and last line (```)
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return text.strip()


@router.get("/{report_type}")
async def get_benchmarks(report_type: str, user: str = Depends(get_current_user)):
    """
    Fetch real-time Amazon seller industry benchmarks via Perplexity Sonar.
    Returns structured metrics for chart comparison.
    """
    if not settings.PERPLEXITY_API_KEY:
        raise HTTPException(503, "Benchmark service not configured — add PERPLEXITY_API_KEY to .env")

    prompt = BENCHMARK_PROMPTS.get(report_type)
    if not prompt:
        raise HTTPException(400, f"No benchmarks available for report type: {report_type!r}")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.perplexity.ai/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.PERPLEXITY_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "sonar",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a data API. Respond with valid JSON only. "
                                "No markdown, no explanation, no code fences."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                },
            )
            resp.raise_for_status()
    except httpx.TimeoutException:
        raise HTTPException(504, "Benchmark service timed out — try again")
    except httpx.HTTPStatusError as e:
        raise HTTPException(502, f"Perplexity API error {e.response.status_code}")

    raw_content = resp.json()["choices"][0]["message"]["content"]
    citations = resp.json().get("citations", [])

    try:
        benchmark_data: dict = json.loads(_strip_code_fences(raw_content))
    except json.JSONDecodeError as e:
        raise HTTPException(500, f"Could not parse benchmark JSON: {e}")

    meta = BENCHMARK_META.get(report_type, {})
    metrics = [
        {
            "key": key,
            "label": meta.get(key, {}).get("label", key),
            "unit": meta.get(key, {}).get("unit", ""),
            "lower_is_better": meta.get(key, {}).get("lower_is_better", False),
            "industry_avg": float(value),
        }
        for key, value in benchmark_data.items()
        if isinstance(value, (int, float))
    ]

    return {
        "report_type": report_type,
        "metrics": metrics,
        "citations": citations[:3],  # top 3 sources
        "source": "Perplexity Sonar — real-time web search",
    }
