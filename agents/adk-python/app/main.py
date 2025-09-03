import os

import httpx
from fastapi import FastAPI

app = FastAPI(title="ADK Agent Gateway (Fraud skeleton)")
MCP_BASE = os.getenv(
    "MCP_BASE",
    "http://mcp-server.agents.svc.cluster.local:8080",
)


@app.get("/healthz")
def health():
    return {"ok": True}


@app.post("/fraud/score")
async def fraud_score(
    user_id: str,
    txn_id: str,
    amount: float,
    merchant: str,
    geo: str,
):
    # Pull context (robust to upstream errors / fallback shapes)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            user_resp = await client.get(
                f"{MCP_BASE}/tools/getUserProfile",
                params={"user_id": user_id},
            )
            txns_resp = await client.get(
                f"{MCP_BASE}/tools/getTransactions",
                params={"user_id": user_id, "limit": 50},
            )
        user = user_resp.json()
        txns = txns_resp.json()
    except Exception:
        user, txns = {}, {"items": []}

    # Normalize counts across shapes
    items = txns.get("items") if isinstance(txns, dict) else None
    recent_count = len(items) if isinstance(items, list) else 0

    # MVP heuristic; (Step 5) we’ll add Gemini
    decision = "REVIEW" if amount > 5000 else "ALLOW"
    reasons = ["Heuristic placeholder; integrate Gemini for real scoring."]

    # Fire-and-forget flag (do not crash if MCP is down)
    if decision != "ALLOW":
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"{MCP_BASE}/tools/flagTransaction",
                    params={"txn_id": txn_id, "reason": "; ".join(reasons)},
                )
        except Exception:
            pass

    return {
        "decision": decision,
        "reasons": reasons,
        "user_summary": {
            "id": user_id,
            "recent_txn_count": recent_count,
            "profile_has_error": (
                bool(user.get("error")) if isinstance(user, dict) else False
            ),
        },
    }
