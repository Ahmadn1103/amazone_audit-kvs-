"""
AI-powered audit endpoints — brand analysis & recommendations via Perplexity multi-query search.

Flow:
  POST /api/audit/analyze
    Step 1 — Multi-query Search: fire 5 targeted queries in ONE Perplexity Search API call,
             gathering web snippets on the brand, niche, competitors, and improvement strategies.
    Step 2 — Synthesis: pass all gathered snippets to a single chat completions call
             to produce structured brand_analysis + recommendations JSON.
"""
import json
import re
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.config import settings
from app.core.dependencies import get_current_user
from app.services.dynamo import save_audit as dynamo_save, list_audits as dynamo_list

router = APIRouter()

PERPLEXITY_SEARCH_URL = "https://api.perplexity.ai/search"
PERPLEXITY_CHAT_URL   = "https://api.perplexity.ai/chat/completions"

REPORT_LABELS = {
    "business_report": "Business Report",
    "active_listings": "Active Listings",
    "account_health":  "Account Health",
    "ads":             "Ads Performance",
    "fba_inventory":   "FBA Inventory",
}


# ── Request model ──────────────────────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    brand_name:    str
    niche:         str = ""
    marketplace:   str = "Amazon US"
    report_type:   str = "business_report"
    audit_purpose: str = ""
    notes:         str = ""


# ── Helpers ────────────────────────────────────────────────────────────────

def _require_key():
    if not settings.PERPLEXITY_API_KEY:
        raise HTTPException(503, "AI service not configured — add PERPLEXITY_API_KEY to .env")


def _extract_json(text: str) -> str:
    """
    Best-effort extraction of a JSON object from an LLM response that may
    include markdown code fences, preamble text, or trailing explanation.
    """
    text = text.strip()

    # Strip ```json ... ``` or ``` ... ``` fences (handles any language tag)
    fence_match = re.search(r"```(?:\w+)?\s*([\s\S]*?)```", text)
    if fence_match:
        text = fence_match.group(1).strip()

    # If it already starts with '{', try it as-is first
    if text.startswith("{"):
        return text

    # Otherwise find the first '{' and the last '}' and extract between them
    start = text.find("{")
    end   = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]

    return text


def _auth_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.PERPLEXITY_API_KEY}",
        "Content-Type": "application/json",
    }


async def _multi_search(
    client: httpx.AsyncClient,
    queries: list[str],
    max_results: int = 5,
) -> list[list[dict]]:
    """
    Call Perplexity multi-query Search API.
    Returns a list of result-lists, one per query (same order).
    Each result has: title, url, snippet, date.
    """
    resp = await client.post(
        PERPLEXITY_SEARCH_URL,
        headers=_auth_headers(),
        json={"query": queries, "max_results": max_results},
    )
    resp.raise_for_status()
    data = resp.json()
    raw = data.get("results", [])

    # Single-query returns a flat list; multi-query returns grouped lists.
    if raw and not isinstance(raw[0], list):
        return [raw]
    return raw


def _format_snippets(results: list[dict], label: str) -> str:
    """Format one query's search results into readable context text."""
    if not results:
        return f"## {label}\n[No results found]"
    lines = [f"## {label}"]
    for r in results:
        title   = r.get("title", "")
        snippet = r.get("snippet", "")
        url     = r.get("url", "")
        date    = r.get("date", "")
        date_str = f" [{date}]" if date else ""
        lines.append(f"- {title}{date_str}: {snippet}\n  Source: {url}")
    return "\n".join(lines)


async def _synthesize(client: httpx.AsyncClient, research_context: str, req: AnalyzeRequest) -> dict:
    """
    Pass all gathered search snippets to Perplexity chat completions
    and get back structured brand_analysis + recommendations JSON.
    """
    rtype        = REPORT_LABELS.get(req.report_type, req.report_type)
    purpose_line = f"\nSeller's stated goal: {req.audit_purpose}" if req.audit_purpose else ""
    notes_line   = f"\nAdditional context: {req.notes}" if req.notes else ""

    user_prompt = (
        f"Based on the following web research about the brand '{req.brand_name}' "
        f"in the '{req.niche or 'general Amazon products'}' niche on {req.marketplace}, "
        f"selling via {rtype} reports:{purpose_line}{notes_line}\n\n"
        f"{research_context}\n\n"
        "Return a single JSON object with EXACTLY these two top-level keys:\n"
        "1. \"brand_analysis\": {\n"
        "     \"summary\": \"<2-3 sentence overview of the brand and its market position>\",\n"
        "     \"competitive_landscape\": \"<2-3 sentences on competitive dynamics and key success drivers>\",\n"
        "     \"top_seller_traits\": [\"<trait>\", \"<trait>\", \"<trait>\", \"<trait>\"]\n"
        "   }\n"
        "2. \"recommendations\": [\n"
        "     {\"title\": \"<short action title>\", "
        "\"description\": \"<1-2 sentence concrete action>\", "
        "\"priority\": \"high\" or \"medium\" or \"low\"},\n"
        "     ... (5-7 recommendations, tailored to this brand/niche)\n"
        "   ]"
    )

    resp = await client.post(
        PERPLEXITY_CHAT_URL,
        headers=_auth_headers(),
        json={
            "model": "sonar-pro",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an expert Amazon seller consultant and structured data API. "
                        "Synthesise the provided web research into actionable insights. "
                        "Respond with valid JSON only — no markdown, no explanation, no code fences."
                    ),
                },
                {"role": "user", "content": user_prompt},
            ],
        },
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return json.loads(_extract_json(content))


# ── Endpoint ───────────────────────────────────────────────────────────────

@router.post("/analyze")
async def analyze(req: AnalyzeRequest, user: str = Depends(get_current_user)):
    """
    Full AI audit: multi-query Perplexity search + synthesis.

    Step 1: Fire 5 targeted queries in a SINGLE Perplexity Search API call.
    Step 2: Synthesise all gathered snippets via one chat completions call.

    Returns brand_analysis, recommendations, raw search_results, and citations.
    """
    _require_key()

    brand  = req.brand_name
    niche  = req.niche or "general Amazon products"
    market = req.marketplace
    rtype  = REPORT_LABELS.get(req.report_type, req.report_type)

    # ── Step 1: Multi-query search ─────────────────────────────────────────
    # Up to 5 queries per the Perplexity multi-query limit.
    queries = [
        f"{brand} Amazon seller brand overview {niche}",
        f"{niche} Amazon top sellers competitive landscape {market} 2024 2025",
        f"Amazon {rtype} improvement best practices strategies {niche} sellers",
        f"{brand} Amazon customer reviews product quality reputation",
        f"Amazon seller {niche} niche growth opportunities trends 2025",
    ]

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:

            try:
                grouped = await _multi_search(client, queries, max_results=5)
            except httpx.HTTPStatusError:
                # Search API unavailable on this plan — proceed with empty context
                grouped = [[] for _ in queries]

            # Collect URLs and build context text from all result groups
            all_urls: list[str] = []
            context_sections: list[str] = []

            for query, results in zip(queries, grouped):
                context_sections.append(_format_snippets(results, query))
                for r in results:
                    if url := r.get("url"):
                        all_urls.append(url)

            research_context = "\n\n".join(context_sections)

            # ── Step 2: Synthesis ──────────────────────────────────────────
            synthesis = await _synthesize(client, research_context, req)

    except httpx.TimeoutException:
        raise HTTPException(504, "AI service timed out — try again")
    except httpx.HTTPStatusError as e:
        raise HTTPException(502, f"Perplexity API error {e.response.status_code}")
    except json.JSONDecodeError:
        raise HTTPException(500, "Could not parse AI synthesis response — try again")

    brand_analysis  = synthesis.get("brand_analysis", {})
    recommendations = synthesis.get("recommendations", [])
    citations       = list(dict.fromkeys(all_urls))[:6]  # deduplicated, top 6

    return {
        "brand_name":  brand,
        "niche":       req.niche,
        "marketplace": market,
        "brand_analysis": {
            "summary":               brand_analysis.get("summary", ""),
            "competitive_landscape": brand_analysis.get("competitive_landscape", ""),
            "top_seller_traits":     brand_analysis.get("top_seller_traits", []),
        },
        "recommendations": recommendations,
        "search_results": [
            {
                "query":   query,
                "results": [
                    {
                        "title":   r.get("title", ""),
                        "url":     r.get("url", ""),
                        "snippet": r.get("snippet", ""),
                        "date":    r.get("date", ""),
                    }
                    for r in results
                ],
            }
            for query, results in zip(queries, grouped)
        ],
        "citations": citations,
    }


# ── Save & List ────────────────────────────────────────────────────────────

class SaveAuditRequest(BaseModel):
    audit_id:         str
    brand_name:       str
    niche:            str  = ""
    marketplace:      str  = "Amazon US"
    report_type:      str  = "business_report"
    audit_purpose:    str  = ""
    notes:            str  = ""
    brand_analysis:   dict = {}
    recommendations:  list = []
    benchmark_metrics: list = []
    csv_metadata:     dict = {}
    citations:        list = []


@router.post("/save")
async def save_audit(req: SaveAuditRequest, user: str = Depends(get_current_user)):
    """Persist a completed audit to DynamoDB."""
    try:
        dynamo_save(user, req.audit_id, req.model_dump())
    except Exception as e:
        raise HTTPException(500, f"Failed to save audit: {e}")
    return {"saved": True}


@router.get("/list")
async def list_audits(user: str = Depends(get_current_user)):
    """Return all saved audits for the current user, newest first."""
    try:
        audits = dynamo_list(user)
    except Exception as e:
        raise HTTPException(500, f"Failed to list audits: {e}")
    return {"audits": audits}
