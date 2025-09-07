from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Literal, Optional

import httpx
from fastapi import FastAPI
from google import genai  # AI Studio (API key)
# Optional imports (both installed in your image)
from google.cloud import aiplatform  # Vertex
from google.genai.types import GenerateContentConfig  # AI Studio
from vertexai.generative_models import GenerativeModel  # Vertex

TITLE = "ADK Agent Gateway (Fraud + Gemini)"
DESCRIPTION = (
    "Fraud scoring microservice used in the GKE Turns10 Hackathon. "
    "Produces a structured JSON assessment, using Vertex AI or Google AI Studio. "
    "Falls back to a deterministic heuristic if AI is unavailable."
)

app = FastAPI(title=TITLE, description=DESCRIPTION)

# Upstream MCP server
MCP_BASE = os.getenv("MCP_BASE", "http://mcp-server.agents.svc.cluster.local:8080")

# Vertex AI env
PROJECT_ID = os.getenv("PROJECT_ID")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "us-central1")
VERTEX_MODEL = os.getenv("VERTEX_MODEL", "gemini-1.5-pro")  # set "disabled" to skip

# Studio env
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
FORCE_STUDIO = os.getenv("FORCE_STUDIO", "").strip() in {"1", "true", "TRUE"}

# Lazy Vertex init
_vertex_inited = False
_vertex_model: Optional[GenerativeModel] = None


def _vertex_init() -> None:
    global _vertex_inited, _vertex_model
    if _vertex_inited:
        return
    if not PROJECT_ID:
        raise RuntimeError("PROJECT_ID env var is required for Vertex AI")
    if not VERTEX_MODEL or VERTEX_MODEL == "disabled":
        raise RuntimeError("VERTEX_MODEL is disabled")
    aiplatform.init(project=PROJECT_ID, location=VERTEX_LOCATION)
    _vertex_model = GenerativeModel(VERTEX_MODEL)
    _vertex_inited = True


def _try_vertex(prompt: dict) -> Optional[dict]:
    """Return parsed JSON from Vertex AI or None on failure."""
    try:
        _vertex_init()
        cfg = {
            "temperature": 0.2,
            "top_p": 0.8,
            "max_output_tokens": 512,
            "response_mime_type": "application/json",
        }
        # Keep schema lightweight for hackathon reliability
        resp = _vertex_model.generate_content(json.dumps(prompt), generation_config=cfg)
        text = resp.candidates[0].content.parts[0].text
        return json.loads(text)
    except Exception:
        return None


def _try_studio(payload: dict) -> dict | None:
    """
    Attempt fraud scoring via Google AI Studio.
    Returns dict or None if Studio unavailable.
    """
    try:
        prompt = """
        You are a fraud detection engine.
        Return ONLY valid JSON with these keys:
        - risk_score: number between 0 and 1
        - decision: "ALLOW" | "REVIEW" | "DECLINE"
        - reasons: array of 1–2 concise phrases (max 8 words each), explaining the decision.

        Example:
        {"risk_score":0.75,"decision":"REVIEW","reasons":["High amount vs user history","Unusual merchant type"]}
        """

        cfg = GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=128,
            response_mime_type="application/json",
        )
        resp = studio_client.models.generate_content(
            model="gemini-1.5-flash",
            contents=prompt,
            config=cfg,
        )
        return json.loads(resp.text)
    except Exception as e:
        logger.warning("Studio fallback failed: %s", e)
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
    # 1) Context from MCP (robust even if upstream 404s)
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
    except Exception:
        user, txns = {}, {"items": []}

    items = txns.get("items") if isinstance(txns, dict) else []
    recent_count = len(items) if isinstance(items, list) else 0

    # 2) Prepare the common prompt
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
    }

    # 3) AI selection:
    #    - If FORCE_STUDIO=1 -> Studio first
    #    - Else if Vertex enabled -> Vertex first
    #    - Else try Studio (if key present)
    #    - Else heuristic
    ai_backend: Literal["vertex", "studio", "heuristic"]

    ai = None
    if FORCE_STUDIO:
        ai = _try_studio(prompt)
        ai_backend = "studio" if ai is not None else "heuristic"
    else:
        if VERTEX_MODEL and VERTEX_MODEL != "disabled":
            ai = _try_vertex(prompt)
            if ai is not None:
                ai_backend = "vertex"
            else:
                # Try Studio as secondary if key is present
                ai = _try_studio(prompt)
                ai_backend = "studio" if ai is not None else "heuristic"
        else:
            ai = _try_studio(prompt)
            ai_backend = "studio" if ai is not None else "heuristic"

    # 4) Interpret or fallback
    if ai is not None:
        risk_score = float(ai.get("risk_score", 0.5))
        decision = ai.get("decision", "REVIEW")
        reasons = ai.get("reasons", ["Model returned no reasons."])
        features_used = ai.get("features_used", [])
    else:
        # Deterministic heuristic
        risk_score = 0.3 if amount <= 5000 else 0.7
        decision = "ALLOW" if risk_score < 0.6 else "REVIEW"
        reasons = [
            "Fallback heuristic (Gemini unavailable).",
            f"amount={amount}",
        ]
        features_used = ["amount_threshold"]

    # 5) Fire-and-forget flagging for non-ALLOW
    if decision != "ALLOW":
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"{MCP_BASE}/tools/flagTransaction",
                    json={
                        "txn_id": txn_id,
                        "user_id": user_id,
                        "amount": amount,
                        "merchant": merchant,
                        "geo": geo,
                        "risk": risk_score,
                        "decision": decision,
                    },
                )
        except Exception:
            # Silent best-effort
            pass

    # Minimal log line for demo clarity
    print(
        json.dumps(
            {
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
        )
    )

    return {
        "risk_score": risk_score,
        "decision": decision,
        "reasons": reasons,
        "features_used": features_used,
        "ai_backend": ai_backend,
        "user_summary": {
            "id": user.get("id", user_id) if isinstance(user, dict) else user_id,
            "recent_txn_count": recent_count,
            "profile_has_error": (
                bool(user.get("error")) if isinstance(user, dict) else False
            ),
        },
    }
