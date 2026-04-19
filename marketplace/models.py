"""
Immutable data models for Agent Commerce Framework.
All dataclasses are frozen — create new instances instead of mutating.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


# --- Service ---

@dataclass(frozen=True)
class PricingConfig:
    """Service pricing configuration."""
    price_per_call: Decimal
    currency: str = "USDC"
    payment_method: str = "x402"  # x402 | stripe | both
    free_tier_calls: int = 0
    bulk_discount: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ServiceListing:
    """A service listed on the marketplace."""
    id: str = field(default_factory=_new_id)
    provider_id: str = ""
    name: str = ""
    description: str = ""
    endpoint: str = ""
    pricing: PricingConfig = field(default_factory=PricingConfig)
    status: str = "active"  # active | paused | reviewing | removed
    category: str = ""
    tags: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    metadata: dict = field(default_factory=dict)


# --- API Key ---

@dataclass(frozen=True)
class APIKey:
    """API key for authentication."""
    key_id: str = ""
    hashed_secret: str = ""
    owner_id: str = ""
    role: str = "buyer"  # admin | provider | buyer
    rate_limit: int = 60  # calls per minute
    wallet_address: Optional[str] = None
    created_at: datetime = field(default_factory=_utcnow)
    expires_at: Optional[datetime] = None


# --- Usage ---

@dataclass(frozen=True)
class UsageRecord:
    """Record of a single API call through the marketplace."""
    id: str = field(default_factory=_new_id)
    buyer_id: str = ""
    service_id: str = ""
    provider_id: str = ""
    timestamp: datetime = field(default_factory=_utcnow)
    latency_ms: int = 0
    status_code: int = 200
    amount_usd: Decimal = Decimal("0")
    payment_method: str = "x402"
    payment_tx: Optional[str] = None


# --- Settlement ---

@dataclass(frozen=True)
class Settlement:
    """Settlement record for provider payouts."""
    id: str = field(default_factory=_new_id)
    provider_id: str = ""
    period_start: datetime = field(default_factory=_utcnow)
    period_end: datetime = field(default_factory=_utcnow)
    total_amount: Decimal = Decimal("0")
    platform_fee: Decimal = Decimal("0")
    net_amount: Decimal = Decimal("0")
    payment_tx: Optional[str] = None
    status: str = "pending"  # pending | processing | completed | failed


# --- Agent Identity ---

@dataclass(frozen=True)
class AgentIdentity:
    """Agent identity on the marketplace."""
    agent_id: str = field(default_factory=_new_id)
    display_name: str = ""
    owner_id: str = ""
    identity_type: str = "api_key_only"  # api_key_only | kya_jwt | did_vc
    capabilities: tuple[str, ...] = ()
    wallet_address: Optional[str] = None
    verified: bool = False
    reputation_score: float = 0.0
    status: str = "active"  # active | suspended | deactivated
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    metadata: dict = field(default_factory=dict)


# --- Reputation ---

@dataclass(frozen=True)
class ReputationRecord:
    """Per-service, per-period reputation record (auto-computed from usage)."""
    id: str = field(default_factory=_new_id)
    agent_id: str = ""
    service_id: str = ""
    overall_score: float = 0.0
    latency_score: float = 0.0
    reliability_score: float = 0.0
    response_quality: float = 0.0
    call_count: int = 0
    period: str = ""  # YYYY-MM or "all-time"
    created_at: datetime = field(default_factory=_utcnow)


# --- Service SLA ---

@dataclass(frozen=True)
class FoundingSeller:
    """Founding Seller badge — permanent, awarded to first 50 providers."""
    id: str = field(default_factory=_new_id)
    provider_id: str = ""
    sequence_number: int = 0  # 1-50
    badge_tier: str = "founding"  # founding (permanent)
    commission_rate: float = 0.08  # 8% (vs standard 10%)
    awarded_at: datetime = field(default_factory=_utcnow)
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ServiceSLA:
    """Service-level agreement for a listing."""
    service_id: str = ""
    max_latency_ms: int = 5000
    min_uptime_pct: float = 99.0
    max_error_rate_pct: float = 5.0
    guaranteed_throughput: int = 100  # calls per minute


# --- Agent Provider ---

@dataclass(frozen=True)
class AgentProvider:
    """An AI agent registered as a service provider."""
    id: str = field(default_factory=_new_id)
    agent_id: str = ""
    owner_email: str = ""
    wallet_address: str = ""
    did: str = ""
    declaration: str = ""
    status: str = "pending_review"
    reputation_score: float = 0.0
    fast_track_eligible: bool = False
    daily_tx_cap: float = 500.0
    probation_ends_at: Optional[datetime] = None
    total_reports: int = 0
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ServiceReview:
    """Automated review result for a service listing."""
    id: str = field(default_factory=_new_id)
    service_id: str = ""
    provider_id: str = ""
    review_type: str = "automated"
    status: str = "pending"
    endpoint_reachable: bool = False
    response_format_valid: bool = False
    response_time_ms: int = 0
    malicious_check_passed: bool = False
    error_details: str = ""
    reviewer_notes: str = ""
    reviewed_at: Optional[datetime] = None
    created_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True)
class EscrowHold:
    """Payment held in escrow before release to Agent Provider."""
    id: str = field(default_factory=_new_id)
    provider_id: str = ""
    service_id: str = ""
    buyer_id: str = ""
    amount: Decimal = Decimal("0")
    currency: str = "USDC"
    status: str = "held"
    usage_record_id: str = ""
    held_at: datetime = field(default_factory=_utcnow)
    release_at: datetime = field(default_factory=_utcnow)
    released_at: Optional[datetime] = None
    created_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True)
class ServiceReport:
    """Report filed against a service by a buyer."""
    id: str = field(default_factory=_new_id)
    service_id: str = ""
    provider_id: str = ""
    reporter_id: str = ""
    reason: str = ""
    details: str = ""
    status: str = "open"
    created_at: datetime = field(default_factory=_utcnow)
