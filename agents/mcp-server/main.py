import os

import httpx
from fastapi import FastAPI

app = FastAPI(title="MCP Server (BoA Tools)")

USERS_API = os.getenv(
    "USERS_API",
    "http://userservice.default.svc.cluster.local",
)
TXN_API = os.getenv(
    "TXN_API",
    "http://transactionhistory.default.svc.cluster.local",
)


@app.get("/healthz")
def health():
    return {"ok": True}


@app.get("/tools/getUserProfile")
async def get_user_profile(user_id: str):
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{USERS_API}/users/{user_id}")
        r.raise_for_status()
        return r.json()


@app.get("/tools/getTransactions")
async def get_transactions(user_id: str, limit: int = 25):
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{TXN_API}/transactions",
            params={"user": user_id, "limit": limit},
        )
        r.raise_for_status()
        return r.json()


@app.post("/tools/flagTransaction")
async def flag_transaction(txn_id: str, reason: str):
    # Stub: in production, write to a review queue or DB
    return {"txn_id": txn_id, "flagged": True, "reason": reason}
