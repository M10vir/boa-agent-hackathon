import json
import os
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI

# Vertex AI
from google.cloud import aiplatform
from vertexai.generative_models import GenerativeModel

# AI Studio (fallback)
from google import genai
from google.genai.types import GenerateContentConfig

app = FastAPI(title="ADK Agent Gateway (Fraud + Gemini)")
MCP_BASE = os.getenv("MCP_BASE", "http://mcp-server.agents.svc.cluster.local:8080")
PROJECT_ID = os.getenv("PROJECT_ID")
LOCATION = os.getenv("VERTEX_LOCATION", "us-central1")
MODEL_NAME = os.getenv("VERTEX_MODEL", "gemini-1.5-pro")  # not used by Studio, but ok

_vertex_inited = False
_model = None

def _vertex_init():
    global _vertex_inited, _model
    if _vertex_inited:
        return
    if not PROJECT_ID:
        raise RuntimeError("PROJECT_ID env var is required for Vertex AI")
    aiplatform.init(project=PROJECT_ID, location=LOCATION)
    _model = GenerativeModel(MODEL_NAME)
    _vertex_inited = True

def _vertex_json(prompt_dict):
    generation_config = {
        "temperature": 0.2,
        "top_p": 0.8,
        "max_output_tokens": 512,
        "response_mime_type": "application/json",
    }
    resp = _model.generate_content(
        json.dumps(prompt_dict),
        generation_config=generation_config,
        safety_settings=[],
    )
    return resp.candidates[0].content.parts[0].text

def _studio_json(prompt_dict):
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY not set")
    client = genai.Client(api_key=api_key)
    cfg = GenerateContentConfig(
        temperature=0.2,
        max_output_tokens=512,
        response_mime_type="application/json",
    )
    r = client.models.generate_content(
        model="gemini-1.5-flash",  # Studio model id (widely available)
        contents=json.dumps(prompt_dict),
        config=cfg,
    )
    # r.text is a JSON string
    return r.text

@app.get("/healthz")
def health():
    return {"ok": True}

@app.post("/fraud/score")
async def fraud_score(user_id: str, txn_id: str, amount: float, merchant: str, geo: str):
    # Context via MCP
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            user_resp = await client.get(f"{MCP_BASE}/tools/getUserProfile", params={"user_id": user_id})
            txns_resp = await client.get(f"{MCP_BASE}/tools/getTransactions", params={"user_id": user_id, "limit": 50})
        user = user_resp.json()
        txns = txns_resp.json()
    except Exception:
        user, txns = {}, {"items": []}
    items = txns.get("items") if isinstance(txns, dict) else []
    recent_count = len(items) if isinstance(items, list) else 0

    # Build prompt
    prompt = {
        "task": "Assess credit/fraud risk for a single card transaction.",
        "transaction": {
            "txn_id": txn_id,
            "amount": amount,
            "merchant": merchant,
            "geo": geo,
            "ts_utc": datetime.now(timezone.utc).isoformat(),
        },
        "user_profile": user,
        "recent_transactions": items[:20],
        "requirements": {
            "risk_score_range": [0.0, 1.0],
            "decision_set": ["ALLOW", "REVIEW", "DECLINE"],
            "provide_top_reasons": True,
        },
        # Lightweight few-shot to ensure `reasons` is filled:
        "example_output": {
            "risk_score": 0.72,
            "decision": "REVIEW",
            "reasons": ["High amount vs typical", "Merchant category anomaly"],
            "features_used": ["amount_vs_recent_mean", "merchant_category"],
        },
    }

    # Try Vertex → fallback to Studio → final heuristic
    ai_backend = "vertex"
    gemini_ok = True
    try:
        _vertex_init()
        jewel = _vertex_json(prompt)
    except Exception:
        try:
            ai_backend = "studio"
            jewel = _studio_json(prompt)
        except Exception:
            gemini_ok = False
            jewel = None

    if jewel:
        try:
            ai = json.loads(jewel)
            risk_score = float(ai.get("risk_score", 0.5))
            decision = ai.get("decision", "REVIEW")
            reasons = ai.get("reasons", ["Model returned no reasons."])
            features_used = ai.get("features_used", [])
        except Exception:
            gemini_ok = False
            risk_score = 0.3 if amount <= 5000 else 0.7
            decision = "ALLOW" if risk_score < 0.6 else "REVIEW"
            reasons = ["Fallback heuristic (could not parse model JSON)."]
            features_used = []
    else:
        risk_score = 0.3 if amount <= 5000 else 0.7
        decision = "ALLOW" if risk_score < 0.6 else "REVIEW"
        reasons = ["Fallback heuristic (Gemini unavailable).", f"amount={amount}"]
        features_used = ["amount_threshold"]

    # Optional: flag if not allowed
    if decision != "ALLOW":
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(f"{MCP_BASE}/tools/flagTransaction", params={"txn_id": txn_id, "reason": "; ".join(reasons)})
        except Exception:
            pass

    # Audit log
    audit = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "txn_id": txn_id, "user_id": user_id, "amount": amount,
        "merchant": merchant, "geo": geo,
        "risk_score": risk_score, "decision": decision,
        "gemini_ok": gemini_ok, "ai_backend": ai_backend,
        "recent_txn_count": recent_count,
    }
    print(json.dumps(audit), flush=True)

    return {
        "risk_score": risk_score, "decision": decision, "reasons": reasons,
        "features_used": features_used, "ai_backend": ai_backend,
        "user_summary": {"id": user_id, "recent_txn_count": recent_count,
                         "profile_has_error": bool(user.get("error")) if isinstance(user, dict) else False},
    } 
