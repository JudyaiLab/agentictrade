"""
Webhook subscription API routes.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.deps import extract_owner

router = APIRouter(tags=["webhooks"])


# --- Request models ---

class SubscribeRequest(BaseModel):
    url: str
    events: list[str]
    secret: str


# --- Routes ---

@router.post("/webhooks", status_code=201)
async def subscribe_webhook(req: SubscribeRequest, request: Request):
    """Create a new webhook subscription. Requires API key."""
    from marketplace.webhooks import WebhookError

    owner_id, _ = extract_owner(request)
    webhook_mgr = request.app.state.webhooks

    try:
        webhook = webhook_mgr.subscribe(
            owner_id=owner_id,
            url=req.url,
            events=req.events,
            secret=req.secret,
        )
        return _webhook_response(webhook)
    except WebhookError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/webhooks")
async def list_webhooks(request: Request):
    """List own webhook subscriptions. Requires API key."""
    owner_id, _ = extract_owner(request)
    webhook_mgr = request.app.state.webhooks

    subscriptions = webhook_mgr.list_subscriptions(owner_id)
    return {
        "webhooks": [_webhook_response(w) for w in subscriptions],
        "count": len(subscriptions),
    }


@router.delete("/webhooks/{webhook_id}")
async def unsubscribe_webhook(webhook_id: str, request: Request):
    """Delete a webhook subscription (owner only). Requires API key."""
    owner_id, _ = extract_owner(request)
    webhook_mgr = request.app.state.webhooks

    removed = webhook_mgr.unsubscribe(webhook_id, owner_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return {"status": "deleted", "webhook_id": webhook_id}


# --- Helpers ---

def _webhook_response(webhook) -> dict:
    return {
        "id": webhook.id,
        "owner_id": webhook.owner_id,
        "url": webhook.url,
        "events": list(webhook.events),
        "active": webhook.active,
        "created_at": webhook.created_at.isoformat(),
    }
