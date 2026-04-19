"""
Template API for AgenticTrade Marketplace
==========================================
Fork this, customize the /predict endpoint, deploy, and list on AgenticTrade.

Quick start:
    pip install -r requirements.txt
    uvicorn main:app --host 0.0.0.0 --port 8080

Then list on AgenticTrade:
    1. Log in at https://agentictrade.io/portal/dashboard
    2. Fill in the wizard: name, endpoint URL, price
    3. Done — AI agents can now discover and pay for your API
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(
    title="My AI API",
    description="Template API for the AgenticTrade marketplace",
    version="1.0.0",
)


# ── Request / Response models ────────────────────────────────

class PredictRequest(BaseModel):
    """Input for your API. Customize these fields."""
    text: str
    max_length: int = 200


class PredictResponse(BaseModel):
    """Output from your API. Customize these fields."""
    result: str
    tokens_used: int
    model: str


# ── Health check (required) ──────────────────────────────────

@app.get("/health")
async def health():
    """Health check — AgenticTrade pings this to verify your API is up."""
    return {"status": "ok"}


# ── Your main endpoint ───────────────────────────────────────

@app.post("/predict", response_model=PredictResponse)
async def predict(req: PredictRequest):
    """
    Replace this with your actual AI logic.

    This template just echoes the input as a demo.
    In production you would call your model here:
      - OpenAI / Claude / local model
      - Image generation
      - Data analysis
      - Any computation you want to sell
    """
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Text input is required")

    # === YOUR LOGIC HERE ===
    result = f"Processed: {req.text[:req.max_length]}"
    tokens_used = len(req.text.split())
    # === END YOUR LOGIC ===

    return PredictResponse(
        result=result,
        tokens_used=tokens_used,
        model="template-v1",
    )


# ── Optional: add more endpoints ─────────────────────────────

@app.get("/models")
async def list_models():
    """List available models (optional, for discovery)."""
    return {
        "models": [
            {"id": "template-v1", "description": "Demo template model"},
        ]
    }
