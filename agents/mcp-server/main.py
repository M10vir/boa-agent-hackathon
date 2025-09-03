import os

import httpx
from fastapi import FastAPI

app = FastAPI(title="MCP Server (BoA Tools)")

USERS_API = os.getenv(
    "USERS_API",
    "http://userservice.default.svc.cluster.local:8080",
)
TXN_API = os.getenv(
    "TXN_API",
    "http://transactionhistory.default.svc.cluster.local:8080",
)


@app.get("/healthz")
def health():
    return {"ok": True}


@app.get("/tools/getUserProfile")
async def get_user_profile(user_id: str):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{USERS_API}/users/{user_id}")
            r.raise_for_status()
            return r.json()
    except Exception as e:
        # Safe fallback so callers never 500
        return {
            "id": user_id,
            "name": "Demo User",
            "email": f"{user_id.lower()}@example.com",
            "error": f"upstream_unreachable: {type(e).__name__}",
        }


@app.get("/tools/getTransactions")
async def get_transactions(user_id: str, limit: int = 25):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{TXN_API}/transactions",
                params={"user": user_id, "limit": limit},
            )
            r.raise_for_status()
            return r.json()
    except Exception as e:
        # Safe fallback shape
        return {
            "user": user_id,
            "items": [
                {
                    "id": "tx-001",
                    "amount": 42.15,
                    "merchant": "Coffee",
                    "geo": "US",
                },
                {
                    "id": "tx-002",
                    "amount": 199.99,
                    "merchant": "Electronics",
                    "geo": "US",
                },
            ],
            "error": f"upstream_unreachable: {type(e).__name__}",
        }


@app.post("/tools/flagTransaction")
async def flag_transaction(txn_id: str, reason: str):
    # Stub: in production, write to a review queue or DB
    return {"txn_id": txn_id, "flagged": True, "reason": reason}
