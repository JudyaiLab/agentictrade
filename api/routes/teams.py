"""
Team Management API routes.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.deps import extract_owner

router = APIRouter(tags=["teams"])


# --- Request models ---

ALLOWED_MEMBER_ROLES = {"leader", "worker", "reviewer", "router"}
ALLOWED_GATE_TYPES = {"quality_score", "latency", "error_rate", "coverage", "custom"}


class CreateTeamRequest(BaseModel):
    name: str
    description: str = ""
    config: dict = {}


class UpdateTeamRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    config: Optional[dict] = None


class AddMemberRequest(BaseModel):
    agent_id: str
    role: str = "worker"
    skills: list[str] = []


class AddRoutingRuleRequest(BaseModel):
    name: str
    keywords: list[str]
    target_agent_id: str
    priority: int = 0


class AddQualityGateRequest(BaseModel):
    gate_type: str
    threshold: float
    gate_order: int = 0
    config: dict = {}


# --- Team CRUD ---

@router.post("/teams", status_code=201)
async def create_team(req: CreateTeamRequest, request: Request):
    """Create a new team."""
    owner_id, _ = extract_owner(request)
    db = request.app.state.db

    if not req.name or not req.name.strip():
        raise HTTPException(status_code=400, detail="Team name is required")
    if len(req.name) > 200:
        raise HTTPException(status_code=400, detail="Team name too long (max 200)")

    now = datetime.now(timezone.utc).isoformat()
    team_id = str(uuid.uuid4())
    db.insert_team({
        "id": team_id,
        "name": req.name,
        "owner_id": owner_id,
        "description": req.description,
        "config": req.config,
        "created_at": now,
        "updated_at": now,
    })

    return {"id": team_id, "name": req.name, "owner_id": owner_id}


@router.get("/teams")
async def list_teams(request: Request, limit: int = 50):
    """List teams owned by the authenticated user."""
    db = request.app.state.db
    limit = min(max(limit, 1), 100)
    try:
        owner_id, _ = extract_owner(request)
    except HTTPException:
        # Unauthenticated — return empty instead of all teams
        return {"teams": [], "count": 0}

    teams = db.list_teams(owner_id=owner_id, limit=limit)
    return {"teams": teams, "count": len(teams)}


@router.get("/teams/{team_id}")
async def get_team(team_id: str, request: Request):
    """Get team details with members, rules, and gates (owner only)."""
    owner_id, _ = extract_owner(request)
    db = request.app.state.db
    team = db.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team["owner_id"] != owner_id:
        raise HTTPException(status_code=403, detail="Only team owner can view details")

    members = db.get_team_members(team_id)
    rules = db.get_routing_rules(team_id)
    gates = db.get_quality_gates(team_id)

    return {
        **team,
        "members": members,
        "routing_rules": rules,
        "quality_gates": gates,
    }


@router.patch("/teams/{team_id}")
async def update_team(team_id: str, req: UpdateTeamRequest, request: Request):
    """Update team (owner only)."""
    owner_id, _ = extract_owner(request)
    db = request.app.state.db

    team = db.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team["owner_id"] != owner_id:
        raise HTTPException(status_code=403, detail="Only team owner can update")

    updates = req.model_dump(exclude_none=True)
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    db.update_team(team_id, updates)

    return db.get_team(team_id)


@router.delete("/teams/{team_id}")
async def delete_team(team_id: str, request: Request):
    """Archive a team (soft delete, owner only)."""
    owner_id, _ = extract_owner(request)
    db = request.app.state.db

    team = db.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team["owner_id"] != owner_id:
        raise HTTPException(status_code=403, detail="Only team owner can delete")

    db.delete_team(team_id)
    return {"status": "archived", "id": team_id}


# --- Members ---

@router.post("/teams/{team_id}/members", status_code=201)
async def add_member(team_id: str, req: AddMemberRequest, request: Request):
    """Add a member to a team."""
    owner_id, _ = extract_owner(request)
    db = request.app.state.db

    team = db.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team["owner_id"] != owner_id:
        raise HTTPException(status_code=403, detail="Only team owner can manage members")

    if req.role not in ALLOWED_MEMBER_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"role must be one of {sorted(ALLOWED_MEMBER_ROLES)}",
        )

    member_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    db.insert_team_member({
        "id": member_id,
        "team_id": team_id,
        "agent_id": req.agent_id,
        "role": req.role,
        "skills": req.skills,
        "joined_at": now,
    })

    return {"id": member_id, "team_id": team_id, "agent_id": req.agent_id}


@router.get("/teams/{team_id}/members")
async def list_members(team_id: str, request: Request):
    """List team members (owner only)."""
    owner_id, _ = extract_owner(request)
    db = request.app.state.db
    team = db.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team["owner_id"] != owner_id:
        raise HTTPException(status_code=403, detail="Only team owner can view members")

    members = db.get_team_members(team_id)
    return {"members": members, "count": len(members)}


@router.delete("/teams/{team_id}/members/{agent_id}")
async def remove_member(team_id: str, agent_id: str, request: Request):
    """Remove a member from a team."""
    owner_id, _ = extract_owner(request)
    db = request.app.state.db

    team = db.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team["owner_id"] != owner_id:
        raise HTTPException(status_code=403, detail="Only team owner can manage members")

    removed = db.remove_team_member(team_id, agent_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Member not found")
    return {"status": "removed"}


# --- Routing Rules ---

@router.post("/teams/{team_id}/rules", status_code=201)
async def add_routing_rule(
    team_id: str, req: AddRoutingRuleRequest, request: Request
):
    """Add a routing rule to a team."""
    owner_id, _ = extract_owner(request)
    db = request.app.state.db

    team = db.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team["owner_id"] != owner_id:
        raise HTTPException(status_code=403, detail="Only team owner can manage rules")

    rule_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    db.insert_routing_rule({
        "id": rule_id,
        "team_id": team_id,
        "name": req.name,
        "keywords": req.keywords,
        "target_agent_id": req.target_agent_id,
        "priority": req.priority,
        "created_at": now,
    })

    return {"id": rule_id, "name": req.name}


@router.get("/teams/{team_id}/rules")
async def list_routing_rules(team_id: str, request: Request):
    """List routing rules for a team (owner only)."""
    owner_id, _ = extract_owner(request)
    db = request.app.state.db
    team = db.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team["owner_id"] != owner_id:
        raise HTTPException(status_code=403, detail="Only team owner can view rules")

    rules = db.get_routing_rules(team_id)
    return {"rules": rules, "count": len(rules)}


@router.delete("/teams/{team_id}/rules/{rule_id}")
async def delete_routing_rule(
    team_id: str, rule_id: str, request: Request
):
    """Delete a routing rule."""
    owner_id, _ = extract_owner(request)
    db = request.app.state.db

    team = db.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team["owner_id"] != owner_id:
        raise HTTPException(status_code=403, detail="Only team owner can manage rules")

    removed = db.delete_routing_rule(rule_id, team_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"status": "deleted"}


# --- Quality Gates ---

@router.post("/teams/{team_id}/gates", status_code=201)
async def add_quality_gate(
    team_id: str, req: AddQualityGateRequest, request: Request
):
    """Add a quality gate to a team."""
    owner_id, _ = extract_owner(request)
    db = request.app.state.db

    team = db.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team["owner_id"] != owner_id:
        raise HTTPException(status_code=403, detail="Only team owner can manage gates")

    if req.gate_type not in ALLOWED_GATE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"gate_type must be one of {sorted(ALLOWED_GATE_TYPES)}",
        )
    if not (0.0 <= req.threshold <= 10.0):
        raise HTTPException(
            status_code=400, detail="threshold must be between 0.0 and 10.0",
        )

    gate_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    db.insert_quality_gate({
        "id": gate_id,
        "team_id": team_id,
        "gate_type": req.gate_type,
        "threshold": req.threshold,
        "gate_order": req.gate_order,
        "config": req.config,
        "created_at": now,
    })

    return {"id": gate_id, "gate_type": req.gate_type}


@router.get("/teams/{team_id}/gates")
async def list_quality_gates(team_id: str, request: Request):
    """List quality gates for a team (owner only)."""
    owner_id, _ = extract_owner(request)
    db = request.app.state.db
    team = db.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team["owner_id"] != owner_id:
        raise HTTPException(status_code=403, detail="Only team owner can view gates")

    gates = db.get_quality_gates(team_id)
    return {"gates": gates, "count": len(gates)}


@router.delete("/teams/{team_id}/gates/{gate_id}")
async def delete_quality_gate(
    team_id: str, gate_id: str, request: Request
):
    """Delete a quality gate."""
    owner_id, _ = extract_owner(request)
    db = request.app.state.db

    team = db.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    if team["owner_id"] != owner_id:
        raise HTTPException(status_code=403, detail="Only team owner can manage gates")

    removed = db.delete_quality_gate(gate_id, team_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Gate not found")
    return {"status": "deleted"}


# --- Templates ---

@router.get("/templates/teams")
async def list_team_templates_route(request: Request):
    """List available team templates."""
    from teamwork.templates import list_team_templates
    return {"templates": list_team_templates()}


@router.get("/templates/services")
async def list_service_templates_route(request: Request):
    """List available service templates."""
    from teamwork.templates import list_service_templates
    return {"templates": list_service_templates()}
