"""
Service Registry — CRUD operations for marketplace listings.
"""
from __future__ import annotations

import ipaddress
import os
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional
from urllib.parse import urlparse

from .db import Database
from .models import FoundingSeller, PricingConfig, ServiceListing

# Internal hosts allowed for platform-owned services
_INTERNAL_ALLOWED = set(
    h.strip() for h in os.environ.get("ACF_INTERNAL_HOSTS", "").split(",")
) - {""}


class RegistryError(Exception):
    """Registry operation errors."""


class ServiceRegistry:
    """Manages service listings on the marketplace."""

    ALLOWED_PAYMENT_METHODS = {"x402", "stripe", "nowpayments", "both"}
    MAX_PRICE = Decimal("100.00")  # 單次呼叫上限

    def __init__(self, db: Database):
        self.db = db

    def register(
        self,
        provider_id: str,
        name: str,
        description: str,
        endpoint: str,
        price_per_call: str | Decimal,
        category: str = "",
        tags: list[str] | None = None,
        payment_method: str = "x402",
        free_tier_calls: int = 0,
        metadata: dict | None = None,
    ) -> ServiceListing:
        """Register a new service on the marketplace."""
        # Validate
        self._validate_endpoint(endpoint)
        price = self._validate_price(price_per_call)
        self._validate_payment_method(payment_method)

        if not name or not name.strip():
            raise RegistryError("Service name is required")
        if not provider_id or not provider_id.strip():
            raise RegistryError("Provider ID is required")

        now = datetime.now(timezone.utc)

        service = ServiceListing(
            id=str(uuid.uuid4()),
            provider_id=provider_id.strip(),
            name=name.strip(),
            description=description.strip(),
            endpoint=endpoint.strip(),
            pricing=PricingConfig(
                price_per_call=price,
                payment_method=payment_method,
                free_tier_calls=free_tier_calls,
            ),
            status="active",
            category=category.strip(),
            tags=tuple(t.strip() for t in (tags or [])),
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )

        # Persist
        self.db.insert_service(self._to_db_dict(service))

        # Auto-award Founding Seller badge if eligible
        self._try_award_founding_seller(provider_id.strip())

        return service

    def get(self, service_id: str) -> Optional[ServiceListing]:
        """Get a service by ID."""
        row = self.db.get_service(service_id)
        if not row:
            return None
        return self._from_db_dict(row)

    def search(
        self,
        query: str | None = None,
        category: str | None = None,
        status: str = "active",
        limit: int = 50,
        offset: int = 0,
    ) -> list[ServiceListing]:
        """Search services."""
        rows = self.db.list_services(
            status=status,
            category=category,
            query=query,
            limit=min(limit, 100),
            offset=max(offset, 0),
        )
        return [self._from_db_dict(r) for r in rows]

    def update(
        self, service_id: str, provider_id: str, **updates
    ) -> Optional[ServiceListing]:
        """Update a service (only owner can update)."""
        existing = self.db.get_service(service_id)
        if not existing:
            return None
        if existing["provider_id"] != provider_id:
            raise RegistryError("Only the service owner can update")

        db_updates = {}
        now = datetime.now(timezone.utc).isoformat()

        if "name" in updates:
            name = updates["name"]
            if not name or not name.strip():
                raise RegistryError("Service name cannot be empty")
            db_updates["name"] = name.strip()

        if "description" in updates:
            db_updates["description"] = updates["description"].strip()

        if "endpoint" in updates:
            self._validate_endpoint(updates["endpoint"])
            db_updates["endpoint"] = updates["endpoint"].strip()

        if "price_per_call" in updates:
            price = self._validate_price(updates["price_per_call"])
            db_updates["price_per_call"] = price

        if "status" in updates:
            if updates["status"] not in ("active", "paused"):
                raise RegistryError("Status must be 'active' or 'paused'")
            db_updates["status"] = updates["status"]

        if "category" in updates:
            db_updates["category"] = updates["category"].strip()

        if "tags" in updates:
            db_updates["tags"] = [t.strip() for t in updates["tags"]]

        db_updates["updated_at"] = now

        self.db.update_service(service_id, db_updates)
        return self.get(service_id)

    def remove(self, service_id: str, provider_id: str) -> bool:
        """Soft-delete a service (only owner can remove)."""
        existing = self.db.get_service(service_id)
        if not existing:
            return False
        if existing["provider_id"] != provider_id:
            raise RegistryError("Only the service owner can remove")
        return self.db.delete_service(service_id)

    # --- Founding Seller ---

    def _try_award_founding_seller(self, provider_id: str) -> Optional[dict]:
        """Award Founding Seller badge if eligible. Silent no-op if not."""
        if self.db.get_founding_seller(provider_id):
            return None  # Already a founding seller

        now = datetime.now(timezone.utc)
        record = {
            "id": str(uuid.uuid4()),
            "provider_id": provider_id,
            "badge_tier": "founding",
            "commission_rate": 0.08,
            "awarded_at": now.isoformat(),
            "metadata": {},
        }
        awarded = self.db.award_founding_seller(record)
        if awarded:
            return record
        return None

    def get_founding_seller(self, provider_id: str) -> Optional[dict]:
        """Get founding seller info for a provider."""
        return self.db.get_founding_seller(provider_id)

    def list_founding_sellers(self) -> list[dict]:
        """List all founding sellers."""
        return self.db.list_founding_sellers()

    def founding_seller_count(self) -> int:
        """Get current count of founding sellers."""
        return self.db.count_founding_sellers()

    def founding_seller_spots_remaining(self) -> int:
        """Get remaining founding seller spots."""
        return max(0, 50 - self.db.count_founding_sellers())

    # --- Validation ---

    @staticmethod
    def _validate_endpoint(endpoint: str) -> None:
        parsed = urlparse(endpoint.strip())
        if parsed.scheme not in ("https", "http"):
            raise RegistryError(
                f"Endpoint must use HTTPS (got {parsed.scheme})"
            )
        if not parsed.hostname:
            raise RegistryError("Endpoint must have a valid hostname")
        # Block internal/private endpoints (SSRF protection)
        hostname = parsed.hostname
        blocked_names = {
            "localhost", "127.0.0.1", "0.0.0.0", "::1",
            "metadata.google.internal", "169.254.169.254",
        }
        if hostname in blocked_names and hostname not in _INTERNAL_ALLOWED:
            raise RegistryError("Endpoint cannot point to a private address")
        try:
            ip = ipaddress.ip_address(hostname)
            if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved) \
                    and hostname not in _INTERNAL_ALLOWED:
                raise RegistryError("Endpoint cannot point to a private address")
        except ValueError:
            pass  # Not an IP address, hostname is fine

    @classmethod
    def _validate_price(cls, price: str | Decimal) -> Decimal:
        try:
            d = Decimal(str(price))
        except (InvalidOperation, ValueError) as e:
            raise RegistryError(f"Invalid price: {price}") from e
        if d < 0:
            raise RegistryError("Price cannot be negative")
        if d > cls.MAX_PRICE:
            raise RegistryError(f"Price cannot exceed ${cls.MAX_PRICE}")
        return d

    @classmethod
    def _validate_payment_method(cls, method: str) -> None:
        if method not in cls.ALLOWED_PAYMENT_METHODS:
            raise RegistryError(
                f"Payment method must be one of: {cls.ALLOWED_PAYMENT_METHODS}"
            )

    # --- Serialization ---

    @staticmethod
    def _to_db_dict(service: ServiceListing) -> dict:
        return {
            "id": service.id,
            "provider_id": service.provider_id,
            "name": service.name,
            "description": service.description,
            "endpoint": service.endpoint,
            "price_per_call": service.pricing.price_per_call,
            "currency": service.pricing.currency,
            "payment_method": service.pricing.payment_method,
            "free_tier_calls": service.pricing.free_tier_calls,
            "status": service.status,
            "category": service.category,
            "tags": list(service.tags),
            "metadata": service.metadata,
            "created_at": service.created_at.isoformat(),
            "updated_at": service.updated_at.isoformat(),
        }

    @staticmethod
    def _from_db_dict(row: dict) -> ServiceListing:
        return ServiceListing(
            id=row["id"],
            provider_id=row["provider_id"],
            name=row["name"],
            description=row.get("description", ""),
            endpoint=row["endpoint"],
            pricing=PricingConfig(
                price_per_call=Decimal(str(row["price_per_call"])),
                currency=row.get("currency", "USDC"),
                payment_method=row.get("payment_method", "x402"),
                free_tier_calls=row.get("free_tier_calls", 0),
            ),
            status=row.get("status", "active"),
            category=row.get("category", ""),
            tags=tuple(row.get("tags", [])),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            metadata=row.get("metadata", {}),
        )
