"""
Negotiation API — MIM-627
Autonomous agent-to-agent price negotiation system. JSON storage.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter(tags=["negotiation"])

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
NEGOTIATION_FILE = DATA_DIR / "negotiations.json"

EXPIRY_MINUTES = 10
MAX_ROUNDS_DEFAULT = 5

VALID_STATUSES = {"open", "accepted", "rejected", "expired"}


# ── Data Models ──────────────────────────────────────────────


@dataclass
class NegotiationRound:
    round_num: int
    proposer: str  # agent_id
    proposed_price_usd: float
    response: str = ""  # "accept" / "counter" / "reject"
    counter_price_usd: float = 0.0
    timestamp: str = ""


@dataclass
class NegotiationSession:
    session_id: str
    buyer_id: str
    seller_id: str
    service_id: str
    status: str = "open"
    rounds: list[dict[str, Any]] = field(default_factory=list)
    max_rounds: int = MAX_ROUNDS_DEFAULT
    created_at: str = ""
    expires_at: str = ""


# ── Storage helpers ──────────────────────────────────────────


def _load_all() -> dict[str, Any]:
    if not NEGOTIATION_FILE.exists():
        return {}
    try:
        return json.loads(NEGOTIATION_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("negotiations.json corrupted, rebuilding empty store")
        return {}


def _save_all(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    NEGOTIATION_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_session(session_id: str) -> NegotiationSession:
    data = _load_all()
    if session_id not in data:
        raise HTTPException(404, f"Negotiation session {session_id} not found")
    rec = data[session_id]
    rounds = rec.pop("rounds", [])
    session = NegotiationSession(**{k: v for k, v in rec.items() if k != "rounds"})
    session.rounds = rounds
    return session


def _save_session(session: NegotiationSession) -> None:
    data = _load_all()
    data[session.session_id] = asdict(session)
    _save_all(data)


def _check_expired(session: NegotiationSession) -> NegotiationSession:
    """Check if session has expired."""
    if session.status != "open":
        return session
    if session.expires_at:
        expires = datetime.fromisoformat(session.expires_at)
        if datetime.now(timezone.utc) > expires:
            session.status = "expired"
            _save_session(session)
    return session


def _get_participant_role(session: NegotiationSession, agent_id: str) -> str:
    """Determine the agent's role in this session."""
    if agent_id == session.buyer_id:
        return "buyer"
    if agent_id == session.seller_id:
        return "seller"
    raise HTTPException(403, f"Agent {agent_id} is not a participant in this negotiation")


# ── API Endpoints ────────────────────────────────────────────


@router.post("/negotiate/start")
async def start_negotiation(body: dict[str, Any]) -> dict[str, Any]:
    """Buyer initiates a negotiation."""
    buyer_id = body.get("buyer_id")
    seller_id = body.get("seller_id")
    service_id = body.get("service_id")
    initial_price = body.get("proposed_price_usd")

    if not all([buyer_id, seller_id, service_id, initial_price is not None]):
        raise HTTPException(400, "buyer_id, seller_id, service_id, and proposed_price_usd are required")

    if buyer_id == seller_id:
        raise HTTPException(400, "Buyer and seller cannot be the same agent")

    try:
        initial_price = float(initial_price)
    except (TypeError, ValueError):
        raise HTTPException(400, "proposed_price_usd must be a number")

    if initial_price <= 0:
        raise HTTPException(400, "proposed_price_usd must be greater than 0")

    now = datetime.now(timezone.utc)
    session_id = str(uuid.uuid4())

    first_round = NegotiationRound(
        round_num=1,
        proposer=buyer_id,
        proposed_price_usd=initial_price,
        timestamp=now.isoformat(),
    )

    session = NegotiationSession(
        session_id=session_id,
        buyer_id=buyer_id,
        seller_id=seller_id,
        service_id=service_id,
        status="open",
        rounds=[asdict(first_round)],
        max_rounds=int(body.get("max_rounds", MAX_ROUNDS_DEFAULT)),
        created_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=EXPIRY_MINUTES)).isoformat(),
    )

    _save_session(session)

    return {
        "status": "created",
        "session_id": session_id,
        "expires_at": session.expires_at,
        "round": 1,
    }


@router.post("/negotiate/{session_id}/counter")
async def counter_offer(session_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Submit a counter-offer."""
    agent_id = body.get("agent_id")
    price = body.get("proposed_price_usd")

    if not agent_id or price is None:
        raise HTTPException(400, "agent_id and proposed_price_usd are required")

    try:
        price = float(price)
    except (TypeError, ValueError):
        raise HTTPException(400, "proposed_price_usd must be a number")

    if price <= 0:
        raise HTTPException(400, "proposed_price_usd must be greater than 0")

    session = _load_session(session_id)
    session = _check_expired(session)

    if session.status != "open":
        raise HTTPException(400, f"Negotiation is closed, status: {session.status}")

    _get_participant_role(session, agent_id)

    if len(session.rounds) >= session.max_rounds:
        session.status = "expired"
        _save_session(session)
        return {"status": "expired", "reason": "Maximum rounds exceeded"}

    last_round = session.rounds[-1]
    if last_round["proposer"] == agent_id:
        raise HTTPException(400, "Cannot submit consecutive offers, wait for the other party")

    last_round["response"] = "counter"
    last_round["counter_price_usd"] = price

    new_round = NegotiationRound(
        round_num=len(session.rounds) + 1,
        proposer=agent_id,
        proposed_price_usd=price,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    session.rounds.append(asdict(new_round))
    _save_session(session)

    return {
        "status": "countered",
        "round": len(session.rounds),
        "proposed_price_usd": price,
    }


@router.post("/negotiate/{session_id}/accept")
async def accept_offer(session_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Accept the current offer and finalize the deal."""
    agent_id = body.get("agent_id")
    if not agent_id:
        raise HTTPException(400, "agent_id is required")

    session = _load_session(session_id)
    session = _check_expired(session)

    if session.status != "open":
        raise HTTPException(400, f"Negotiation is closed, status: {session.status}")

    _get_participant_role(session, agent_id)

    last_round = session.rounds[-1]
    if last_round["proposer"] == agent_id:
        raise HTTPException(400, "Cannot accept your own offer, wait for the other party")

    last_round["response"] = "accept"
    session.status = "accepted"
    final_price = last_round["proposed_price_usd"]
    _save_session(session)

    return {
        "status": "accepted",
        "final_price": final_price,
        "total_rounds": len(session.rounds),
        "buyer_id": session.buyer_id,
        "seller_id": session.seller_id,
        "service_id": session.service_id,
    }


@router.post("/negotiate/{session_id}/reject")
async def reject_negotiation(session_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Reject the negotiation and close the session."""
    agent_id = body.get("agent_id")
    if not agent_id:
        raise HTTPException(400, "agent_id is required")

    session = _load_session(session_id)

    if session.status != "open":
        raise HTTPException(400, f"Negotiation is closed, status: {session.status}")

    _get_participant_role(session, agent_id)

    last_round = session.rounds[-1]
    last_round["response"] = "reject"
    session.status = "rejected"
    _save_session(session)

    return {
        "status": "rejected",
        "rejected_by": agent_id,
        "total_rounds": len(session.rounds),
    }


@router.get("/negotiate/{session_id}")
async def get_negotiation(session_id: str) -> dict[str, Any]:
    """Query negotiation status."""
    session = _load_session(session_id)
    session = _check_expired(session)
    return asdict(session)
