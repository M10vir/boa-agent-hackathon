# agents/adk-python/app/main.py
import json
import os
from datetime import datetime, timezone
from typing import List, Literal, Optional

import httpx
from fastapi import FastAPI
# Gemini API (AI Studio) fallback
from google import genai
# Vertex AI (prefer when available)
from google.cloud import aiplatform
from google.genai.types import GenerateContentConfig
from pydantic import BaseModel, Field
from vertexai.generative_models import GenerativeModel


def _try_studio(prompt: dict) -> dict | None:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return None
    try:
        client = genai.Client(api_key=api_key)
        cfg = GenerateContentConfig(
            temperature=0.2,
            top_p=0.8,
            max_output_tokens=512,
            response_mime_type="application/json",
        )
        model = os.getenv("STUDIO_MODEL", "gemini-1.5-flash")
        res = client.models.generate_content(
            model=model, contents=json.dumps(prompt), config=cfg
        )
        return json.loads(res.text)
    except Exception:
        return None


app = FastAPI(
    title="ADK Agent Gateway — Fraud Scoring (Bank of Anthos + Gemini)",
    description=(
        "**What it is:** An agentic fraud-risk microservice for Bank of Anthos—"
        "enriches context via MCP and scores with Gemini.  \n"
        "**How it works:** Prefers Vertex AI; falls back to Gemini API; "
        "guaranteed heuristic as last resort. Returns structured JSON decisions."
    ),
)

# ---- Config ----
MCP_BASE = os.getenv("MCP_BASE", "http://mcp-server.agents.svc.cluster.local:8080")
PROJECT_ID = os.getenv("PROJECT_ID")
LOCATION = os.getenv("VERTEX_LOCATION", "us-central1")
MODEL_NAME = os.getenv("VERTEX_MODEL", "gemini-1.5-pro")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Lazy singletons
_vertex_inited = False
_vertex_model: Optional[GenerativeModel] = None
_studio_client: Optional[genai.Client] = None


# ---- Pydantic models (stable schema for judges) ----
class UserSummary(BaseModel):
    id: str = Field(..., description="User ID")
    recent_txn_count: int = Field(..., description="Recent transactions considered")
    profile_has_error: bool = Field(False, description="True if profile fetch failed")


class FraudScoreResponse(BaseModel):
    risk_score: float = Field(
        ..., ge=0.0, le=1.0, description="0=low risk, 1=high risk"
    )
    decision: Literal["ALLOW", "REVIEW", "DECLINE"]
    reasons: List[str]
    features_used: List[str] = []
    ai_backend: Literal["vertex", "studio", "heuristic"]
    user_summary: UserSummary


# ---- Helpers ----
def _vertex_init() -> None:
    """Initialize Vertex AI once per process."""
    global _vertex_inited, _vertex_model
    if _vertex_inited:
        return
    if not PROJECT_ID:
        raise RuntimeError("PROJECT_ID is required for Vertex AI")
    aiplatform.init(project=PROJECT_ID, location=LOCATION)
    _vertex_model = GenerativeModel(MODEL_NAME)
    _vertex_inited = True


def _studio_init() -> None:
    """Initialize Gemini AI Studio once per process."""
    global _studio_client
    if _studio_client is None:
        if not GOOGLE_API_KEY:
            raise RuntimeError("GOOGLE_API_KEY missing for Studio fallback")
        _studio_client = genai.Client(api_key=GOOGLE_API_KEY)


def _prompt(
    txn_id: str, amount: float, merchant: str, geo: str, user: dict, items: list
) -> dict:
    return {
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
            "json_only": True,
        },
    }


# ---- Endpoints ----
@app.get("/healthz", summary="Liveness probe", tags=["internal"])
def health():
    return {"ok": True}


@app.post(
    "/fraud/score",
    response_model=FraudScoreResponse,
    summary="Score a transaction for fraud/credit risk",
    tags=["fraud"],
    description=(
        "Fetches context from Bank of Anthos via MCP, then asks Gemini to produce a "
        "structured JSON assessment. Falls back to a deterministic heuristic if AI is unavailable."
    ),
)
async def fraud_score(
    user_id: str, txn_id: str, amount: float, merchant: str, geo: str
):
    # 1) Context from MCP (robust even if upstream returns 404)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            user_resp = await client.get(
                f"{MCP_BASE}/tools/getUserProfile", params={"user_id": user_id}
            )
            txns_resp = await client.get(
                f"{MCP_BASE}/tools/getTransactions",
                params={"user_id": user_id, "limit": 50},
            )
        user = user_resp.json()
        txns = txns_resp.json()
        items = txns.get("items") if isinstance(txns, dict) else []
        profile_has_error = bool(user.get("error"))
    except Exception:
        user, items, profile_has_error = {}, [], True

    recent_count = len(items) if isinstance(items, list) else 0
    payload = _prompt(txn_id, amount, merchant, geo, user, items)

    # 2) Vertex AI (preferred)
    try:
        _vertex_init()
        generation_config = {
            "temperature": 0.2,
            "top_p": 0.8,
            "max_output_tokens": 512,
            "response_mime_type": "application/json",
        }
        schema = {
            "type": "object",
            "properties": {
                "risk_score": {"type": "number"},
                "decision": {"type": "string"},
                "reasons": {"type": "array", "items": {"type": "string"}},
                "features_used": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["risk_score", "decision", "reasons"],
            "additionalProperties": False,
        }
        v_resp = _vertex_model.generate_content(  # type: ignore[union-attr]
            json.dumps(payload),
            generation_config=generation_config,
            safety_settings=[],
            response_schema=schema,
        )
        v_json = json.loads(v_resp.candidates[0].content.parts[0].text)
        return FraudScoreResponse(
            risk_score=float(v_json.get("risk_score", 0.5)),
            decision=v_json.get("decision", "REVIEW"),
            reasons=v_json.get("reasons", ["Model returned no reasons."]),
            features_used=v_json.get("features_used", []),
            ai_backend="vertex",
            user_summary=UserSummary(
                id=user_id,
                recent_txn_count=recent_count,
                profile_has_error=profile_has_error,
            ),
        )
    except Exception:
        # 3) AI Studio fallback
        try:
            _studio_init()
            cfg = GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=512,
                response_mime_type="application/json",
            )
            s_resp = _studio_client.models.generate_content(  # type: ignore[arg-type]
                model=MODEL_NAME, contents=json.dumps(payload), config=cfg
            )
            s_json = json.loads(s_resp.text)
            return FraudScoreResponse(
                risk_score=float(s_json.get("risk_score", 0.6)),
                decision=s_json.get("decision", "REVIEW"),
                reasons=s_json.get("reasons", ["Model returned no reasons."]),
                features_used=s_json.get("features_used", []),
                ai_backend="studio",
                user_summary=UserSummary(
                    id=user_id,
                    recent_txn_count=recent_count,
                    profile_has_error=profile_has_error,
                ),
            )
        except Exception:
            # 4) Deterministic heuristic (always available)
            risk_score = 0.3 if amount <= 5000 else 0.7
            decision = "ALLOW" if risk_score < 0.6 else "REVIEW"
            reasons = ["Fallback heuristic (Gemini unavailable).", f"amount={amount}"]
            return FraudScoreResponse(
                risk_score=risk_score,
                decision=decision,
                reasons=reasons,
                features_used=["amount_threshold"],
                ai_backend="heuristic",
                user_summary=UserSummary(
                    id=user_id,
                    recent_txn_count=recent_count,
                    profile_has_error=profile_has_error,
                ),
            )
