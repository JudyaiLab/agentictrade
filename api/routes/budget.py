"""
Budget Governor API — MIM-625
Agent 預算控制，防止 Agent 超支。JSON 存儲。
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter(tags=["budget"])

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
BUDGET_FILE = DATA_DIR / "budget.json"


# ── Data Models ──────────────────────────────────────────────


@dataclass
class BudgetPolicy:
    agent_id: str
    daily_limit_usd: float = 1.0
    per_tx_limit_usd: float = 0.01
    monthly_limit_usd: float = 10.0
    spent_today_usd: float = 0.0
    spent_month_usd: float = 0.0
    last_reset_date: str = ""
    last_reset_month: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class BudgetCheckResult:
    allowed: bool
    reason: str = ""
    remaining_daily: float = 0.0
    remaining_monthly: float = 0.0


# ── Storage helpers ──────────────────────────────────────────


def _load_all() -> dict[str, Any]:
    if not BUDGET_FILE.exists():
        return {}
    try:
        return json.loads(BUDGET_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("budget.json 損毀，重建空檔")
        return {}


def _save_all(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BUDGET_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_policy(agent_id: str) -> BudgetPolicy:
    data = _load_all()
    if agent_id not in data:
        raise HTTPException(404, f"Agent {agent_id} 尚未設定預算政策")
    rec = data[agent_id]
    history = rec.pop("history", [])
    policy = BudgetPolicy(**{k: v for k, v in rec.items() if k != "history"})
    policy.history = history
    return policy


def _save_policy(policy: BudgetPolicy) -> None:
    data = _load_all()
    data[policy.agent_id] = asdict(policy)
    _save_all(data)


# ── Core logic ───────────────────────────────────────────────


def _auto_reset(policy: BudgetPolicy) -> BudgetPolicy:
    """跨天/跨月自動重置計數器。"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    month = today[:7]

    if policy.last_reset_date != today:
        policy.spent_today_usd = 0.0
        policy.last_reset_date = today

    if policy.last_reset_month != month:
        policy.spent_month_usd = 0.0
        policy.last_reset_month = month

    return policy


def check_budget(policy: BudgetPolicy, amount_usd: float) -> BudgetCheckResult:
    policy = _auto_reset(policy)

    if amount_usd <= 0:
        return BudgetCheckResult(False, reason="金額必須大於 0")

    if amount_usd > policy.per_tx_limit_usd:
        return BudgetCheckResult(
            False,
            reason=f"單筆 ${amount_usd:.4f} 超過上限 ${policy.per_tx_limit_usd:.4f}",
        )

    new_daily = policy.spent_today_usd + amount_usd
    if new_daily > policy.daily_limit_usd:
        return BudgetCheckResult(
            False,
            reason=f"今日累計 ${new_daily:.4f} 將超過每日上限 ${policy.daily_limit_usd:.4f}",
        )

    new_monthly = policy.spent_month_usd + amount_usd
    if new_monthly > policy.monthly_limit_usd:
        return BudgetCheckResult(
            False,
            reason=f"本月累計 ${new_monthly:.4f} 將超過月上限 ${policy.monthly_limit_usd:.4f}",
        )

    return BudgetCheckResult(
        allowed=True,
        remaining_daily=policy.daily_limit_usd - new_daily,
        remaining_monthly=policy.monthly_limit_usd - new_monthly,
    )


# ── API Endpoints ────────────────────────────────────────────


@router.get("/budget/{agent_id}")
async def get_budget(agent_id: str) -> dict[str, Any]:
    """查詢 Agent 預算狀態。"""
    policy = _load_policy(agent_id)
    policy = _auto_reset(policy)
    _save_policy(policy)
    result = asdict(policy)
    result.pop("history", None)
    return result


@router.post("/budget/{agent_id}")
async def set_budget(agent_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """設定或更新 Agent 預算政策。"""
    data = _load_all()
    existing = data.get(agent_id, {})

    policy = BudgetPolicy(
        agent_id=agent_id,
        daily_limit_usd=body.get("daily_limit_usd", existing.get("daily_limit_usd", 1.0)),
        per_tx_limit_usd=body.get("per_tx_limit_usd", existing.get("per_tx_limit_usd", 0.01)),
        monthly_limit_usd=body.get("monthly_limit_usd", existing.get("monthly_limit_usd", 10.0)),
        spent_today_usd=existing.get("spent_today_usd", 0.0),
        spent_month_usd=existing.get("spent_month_usd", 0.0),
        last_reset_date=existing.get("last_reset_date", ""),
        last_reset_month=existing.get("last_reset_month", ""),
        history=existing.get("history", []),
    )

    _save_policy(policy)
    return {"status": "ok", "agent_id": agent_id, "policy": asdict(policy)}


@router.post("/budget/{agent_id}/check")
async def check_budget_endpoint(agent_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """檢查某筆交易是否在預算內。"""
    amount = body.get("amount_usd")
    if amount is None:
        raise HTTPException(400, "缺少 amount_usd 欄位")

    try:
        amount = float(amount)
    except (TypeError, ValueError):
        raise HTTPException(400, "amount_usd 必須為數字")

    policy = _load_policy(agent_id)
    result = check_budget(policy, amount)
    _save_policy(policy)
    return asdict(result)


@router.post("/budget/{agent_id}/spend")
async def record_spend(agent_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """記錄一筆支出。先檢查預算，通過才記帳。"""
    amount = body.get("amount_usd")
    if amount is None:
        raise HTTPException(400, "缺少 amount_usd 欄位")

    try:
        amount = float(amount)
    except (TypeError, ValueError):
        raise HTTPException(400, "amount_usd 必須為數字")

    description = body.get("description", "")

    policy = _load_policy(agent_id)
    result = check_budget(policy, amount)

    if not result.allowed:
        raise HTTPException(403, f"預算不足: {result.reason}")

    policy.spent_today_usd += amount
    policy.spent_month_usd += amount
    policy.history.append({
        "amount_usd": amount,
        "description": description,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    _save_policy(policy)

    return {
        "status": "recorded",
        "amount_usd": amount,
        "remaining_daily": result.remaining_daily,
        "remaining_monthly": result.remaining_monthly,
    }


@router.get("/budget/{agent_id}/history")
async def get_budget_history(
    agent_id: str,
    limit: int = 50,
) -> dict[str, Any]:
    """查詢支出歷史。"""
    policy = _load_policy(agent_id)
    history = policy.history[-limit:]
    return {
        "agent_id": agent_id,
        "total_records": len(policy.history),
        "records": history,
    }
