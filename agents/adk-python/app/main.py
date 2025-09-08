import json
import logging
import os
from datetime import datetime, timezone
from typing import Literal, Optional

import httpx
from fastapi import FastAPI

# ==== Logging ====
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger("adk-gateway")

# ==== Config ====
MCP_BASE = os.getenv("MCP_BASE", "http://mcp-server.agents.svc.cluster.local:8080")

# Vertex AI
PROJECT_ID = os.getenv("PROJECT_ID", "")
LOCATION = os.getenv("VERTEX_LOCATION", "us-central1")
MODEL_NAME = os.getenv(
    "VERTEX_MODEL", "gemini-1.5-pro-002"
)  # set to "disabled" to skip Vertex

# Studio
STUDIO_MODEL = os.getenv("STUDIO_MODEL", "gemini-1.5-flash")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
FORCE_STUDIO = os.getenv("FORCE_STUDIO", "0") == "1"

# ==== FastAPI App ====
app = FastAPI(
    title="ADK Agent Gateway (Fraud + Gemini)",
    description=(
        "MVP fraud scoring service that fuses MCP context with Gemini reasoning. "
        "Returns a structured risk score, decision, and brief, judge-friendly reasons."
    ),
)

# ==== Optional Studio client (lazy) ====
try:
    from google import genai
    from google.genai.types import GenerateContentConfig
except Exception:  # keep boot resilient if lib missing
    genai = None
    GenerateContentConfig = None

_STUDIO_CLIENT: Optional["genai.Client"] = None


def _get_studio_client() -> Optional["genai.Client"]:
    """Create once and reuse; returns None if not possible."""
    global _STUDIO_CLIENT
    if _STUDIO_CLIENT is not None:
        return _STUDIO_CLIENT
    if genai is None:
        logger.warning("google-genai library not available in image; Studio disabled.")
        return None
    api_key = GOOGLE_API_KEY
    if not api_key:
        logger.warning("GOOGLE_API_KEY not set; Studio disabled.")
        return None
    _STUDIO_CLIENT = genai.Client(api_key=api_key)
    return _STUDIO_CLIENT


def _try_studio(prompt_dict: dict) -> Optional[dict]:
    """
    Ask Google AI Studio to return strictly JSON.
    Returns parsed dict on success, or None on any error.
    """
    client = _get_studio_client()
    if not client or GenerateContentConfig is None:
        return None
    try:
        cfg = GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=512,
            response_mime_type="application/json",
        )
        resp = client.models.generate_content(
            model=STUDIO_MODEL,
            contents=json.dumps(prompt_dict),
            config=cfg,
        )
        return json.loads(resp.text)  # strict parse
    except Exception as e:
        logger.warning("Studio generate_content failed: %s", e)
        return None


# ==== Optional Vertex client (lazy) ====
_vertex_inited = False
_vertex_model = None
try:
    from google.cloud import aiplatform
    from vertexai.generative_models import GenerativeModel
except Exception:  # image may not have vertex installed; keep resilient
    aiplatform = None
    GenerativeModel = None


def _vertex_available() -> bool:
    if MODEL_NAME.lower() == "disabled":
        return False
    if not PROJECT_ID:
        return False
    return aiplatform is not None and GenerativeModel is not None


def _vertex_init() -> None:
    global _vertex_inited, _vertex_model
    if _vertex_inited:
        return
    if not _vertex_available():
        raise RuntimeError("Vertex not available (model disabled or libs missing).")
    aiplatform.init(project=PROJECT_ID, location=LOCATION)
    _vertex_model = GenerativeModel(MODEL_NAME)
    _vertex_inited = True


def _try_vertex(prompt_dict: dict) -> Optional[dict]:
    """Ask Vertex to return JSON using response_schema; returns dict or None."""
    if not _vertex_available():
        return None
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
        resp = _vertex_model.generate_content(
            json.dumps(prompt_dict),
            generation_config=generation_config,
            safety_settings=[],  # minimal for hackathon
            response_schema=schema,
        )
        jewel = resp.candidates[0].content.parts[0].text
        return json.loads(jewel)
    except Exception as e:
        logger.warning("Vertex generate_content failed: %s", e)
        return None


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
    # 1) Context from MCP (robust)
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
    except Exception as e:
        logger.warning("MCP calls failed: %s", e)
        user, items = {}, []

    recent_count = len(items) if isinstance(items, list) else 0

    # 2) Build prompt (shared for Vertex/Studio)
    prompt = {
        "task": "Assess credit/fraud risk for a single card transaction. "
        "Return ONLY JSON per schema. Be concise with reasons.",
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
            "reason_style": "short, concrete, model-agnostic",
            "reason_count": 2,
        },
        "schema": {  # self-instruction for Studio
            "type": "object",
            "properties": {
                "risk_score": {"type": "number"},
                "decision": {"type": "string"},
                "reasons": {"type": "array", "items": {"type": "string"}},
                "features_used": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["risk_score", "decision", "reasons"],
            "additionalProperties": False,
        },
        "examples": [
            {
                "risk_score": 0.18,
                "decision": "ALLOW",
                "reasons": ["Small amount", "Matches user pattern"],
            },
            {
                "risk_score": 0.72,
                "decision": "REVIEW",
                "reasons": ["Unusual amount", "New merchant geography"],
            },
        ],
    }

    # 3) AI decision logic
    risk_score: float
    decision: Literal["ALLOW", "REVIEW", "DECLINE"]
    reasons: list[str]
    features_used: list[str]
    ai_backend: Literal["vertex", "studio", "heuristic"]

    # 3.a) Force Studio for demo if requested
    if FORCE_STUDIO:
        ai = _try_studio(prompt)
        if ai is not None:
            risk_score = float(ai.get("risk_score", 0.5))
            decision = ai.get("decision", "REVIEW")
            reasons = ai.get("reasons", ["Model returned no reasons."])
            features_used = ai.get("features_used", [])
            ai_backend = "studio"
        else:
            # deterministic fallback
            risk_score = 0.3 if amount <= 5000 else 0.7
            decision = "ALLOW" if risk_score < 0.6 else "REVIEW"
            reasons = ["Fallback heuristic (Gemini unavailable).", f"amount={amount}"]
            features_used = ["amount_threshold"]
            ai_backend = "heuristic"
    else:
        # 3.b) Vertex first (if available)
        ai = _try_vertex(prompt)
        if ai is not None:
            risk_score = float(ai.get("risk_score", 0.5))
            decision = ai.get("decision", "REVIEW")
            reasons = ai.get("reasons", ["Model returned no reasons."])
            features_used = ai.get("features_used", [])
            ai_backend = "vertex"
        else:
            # 3.c) Studio next
            ai = _try_studio(prompt)
            if ai is not None:
                risk_score = float(ai.get("risk_score", 0.5))
                decision = ai.get("decision", "REVIEW")
                reasons = ai.get("reasons", ["Model returned no reasons."])
                features_used = ai.get("features_used", [])
                ai_backend = "studio"
            else:
                # 3.d) Deterministic heuristic (always available)
                risk_score = 0.3 if amount <= 5000 else 0.7
                decision = "ALLOW" if risk_score < 0.6 else "REVIEW"
                reasons = [
                    "Fallback heuristic (Gemini unavailable).",
                    f"amount={amount}",
                ]
                features_used = ["amount_threshold"]
                ai_backend = "heuristic"

    # 4) Fire-and-forget flagging if needed
    if decision != "ALLOW":
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"{MCP_BASE}/tools/flagTransaction",
                    json={
                        "user_id": user_id,
                        "txn_id": txn_id,
                        "amount": amount,
                        "merchant": merchant,
                        "geo": geo,
                        "risk_score": risk_score,
                        "decision": decision,
                    },
                )
        except Exception as e:
            logger.warning("flagTransaction failed: %s", e)

    # 5) Structured response + simple audit log
    log_line = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "txn_id": txn_id,
        "user_id": user_id,
        "amount": amount,
        "merchant": merchant,
        "geo": geo,
        "risk_score": risk_score,
        "decision": decision,
        "ai_backend": ai_backend,
        "recent_txn_count": recent_count,
    }
    logger.info(json.dumps(log_line))

    return {
        "risk_score": risk_score,
        "decision": decision,
        "reasons": reasons,
        "features_used": features_used,
        "ai_backend": ai_backend,
        "user_summary": {
            "id": user_id,
            "recent_txn_count": recent_count,
            "profile_has_error": not bool(user),
        },
    }
