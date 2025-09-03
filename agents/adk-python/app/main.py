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
    async with httpx.AsyncClient(timeout=15) as client:
        user = (
            await client.get(
                f"{MCP_BASE}/tools/getUserProfile",
                params={"user_id": user_id},
            )
        ).json()
        txns = (
            await client.get(
                f"{MCP_BASE}/tools/getTransactions",
                params={"user_id": user_id, "limit": 50},
            )
        ).json()

    # Silence "assigned but never used" until Gemini integration (Step 5/6).
    _ = user

    # MVP heuristic; Step 6 will swap in Vertex AI Gemini
    decision = "REVIEW" if amount > 5000 else "ALLOW"
    reasons = ["Heuristic placeholder; integrate Gemini for real scoring."]

    if decision != "ALLOW":
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{MCP_BASE}/tools/flagTransaction",
                params={"txn_id": txn_id, "reason": "; ".join(reasons)},
            )

    return {
        "decision": decision,
        "reasons": reasons,
        "user_summary": {"id": user_id, "recent_txn_count": len(txns)},
    }
