"""
Enhanced Service Discovery — search, filter, recommendations, trending.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from .db import Database
from .models import ServiceListing
from .registry import ServiceRegistry

ALLOWED_SORT = {"created_at", "price", "name"}


class DiscoveryEngine:
    """
    Enhanced service discovery with filtering, categories, and trending.
    Wraps ServiceRegistry with richer query capabilities.
    """

    def __init__(self, db: Database, registry: ServiceRegistry):
        self.db = db
        self.registry = registry

    def search(
        self,
        query: str | None = None,
        category: str | None = None,
        tags: list[str] | None = None,
        min_price: str | None = None,
        max_price: str | None = None,
        payment_method: str | None = None,
        has_free_tier: bool | None = None,
        sort_by: str = "created_at",  # created_at | price | name
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """
        Enhanced search with multiple filters.
        Returns dict with services list and metadata.
        """
        # Validate sort_by before any DB query
        if sort_by not in ALLOWED_SORT:
            raise ValueError(f"sort_by must be one of {ALLOWED_SORT}")

        # Paginate through all matching services (registry caps each page
        # at 100, so we fetch pages until exhausted).
        services: list = []
        _page_size = 100
        _page_offset = 0
        while True:
            page = self.registry.search(
                query=query,
                category=category,
                status="active",
                limit=_page_size,
                offset=_page_offset,
            )
            services.extend(page)
            if len(page) < _page_size:
                break
            _page_offset += _page_size

        # Apply additional filters
        filtered = services

        if tags:
            tag_set = set(t.lower() for t in tags)
            filtered = [
                s for s in filtered
                if tag_set.intersection(t.lower() for t in s.tags)
            ]

        if min_price is not None:
            try:
                min_p = Decimal(str(min_price))
            except InvalidOperation:
                raise ValueError(f"Invalid min_price: {min_price!r}")
            filtered = [
                s for s in filtered
                if s.pricing.price_per_call >= min_p
            ]

        if max_price is not None:
            try:
                max_p = Decimal(str(max_price))
            except InvalidOperation:
                raise ValueError(f"Invalid max_price: {max_price!r}")
            filtered = [
                s for s in filtered
                if s.pricing.price_per_call <= max_p
            ]

        if payment_method:
            filtered = [
                s for s in filtered
                if s.pricing.payment_method in (payment_method, "both")
            ]

        if has_free_tier is True:
            filtered = [
                s for s in filtered
                if s.pricing.free_tier_calls > 0
            ]
        elif has_free_tier is False:
            filtered = [
                s for s in filtered
                if s.pricing.free_tier_calls == 0
            ]

        # Sort
        if sort_by == "price":
            filtered.sort(key=lambda s: s.pricing.price_per_call)
        elif sort_by == "name":
            filtered.sort(key=lambda s: s.name.lower())
        else:
            filtered.sort(key=lambda s: s.created_at, reverse=True)

        # Paginate
        total = len(filtered)
        page = filtered[offset:offset + limit]

        return {
            "services": page,
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    def get_categories(self) -> list[dict]:
        """Get all categories with service counts."""
        all_services = self.registry.search(status="active", limit=1000)
        category_counts: dict[str, int] = {}
        for s in all_services:
            cat = s.category or "uncategorized"
            category_counts[cat] = category_counts.get(cat, 0) + 1

        return sorted(
            [{"category": k, "count": v} for k, v in category_counts.items()],
            key=lambda x: x["count"],
            reverse=True,
        )

    def get_trending(self, limit: int = 10, days: int = 7) -> list[dict]:
        """
        Get trending services based on recent usage volume.
        Queries usage_records for the most-called services within a time window.
        """
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.db.connect() as conn:
            rows = conn.execute(
                """SELECT service_id, COUNT(*) as call_count,
                          AVG(latency_ms) as avg_latency
                   FROM usage_records
                   WHERE status_code < 500
                     AND timestamp >= ?
                   GROUP BY service_id
                   ORDER BY call_count DESC
                   LIMIT ?""",
                (since, limit),
            ).fetchall()

        results = []
        for row in rows:
            service = self.registry.get(row["service_id"])
            if service and service.status == "active":
                results.append({
                    "service": service,
                    "call_count": row["call_count"],
                    "avg_latency_ms": round(row["avg_latency"] or 0, 1),
                })

        return results

    def get_recommendations(
        self, agent_id: str, limit: int = 5
    ) -> list[ServiceListing]:
        """
        Recommend services to an agent based on their usage history.
        Simple approach: find categories the agent uses most,
        then suggest other services in those categories.
        """
        # Get agent's usage patterns
        with self.db.connect() as conn:
            used_rows = conn.execute(
                """SELECT DISTINCT service_id
                   FROM usage_records
                   WHERE buyer_id = ?
                   LIMIT 50""",
                (agent_id,),
            ).fetchall()

        used_service_ids = {row["service_id"] for row in used_rows}

        # Find categories from used services
        categories: dict[str, int] = {}
        for sid in used_service_ids:
            service = self.registry.get(sid)
            if service:
                cat = service.category or "uncategorized"
                categories[cat] = categories.get(cat, 0) + 1

        if not categories:
            # No usage history — return newest services
            return self.registry.search(status="active", limit=limit)

        # Get top category
        top_categories = sorted(categories, key=categories.get, reverse=True)[:3]

        recommendations = []
        for cat in top_categories:
            services = self.registry.search(category=cat, status="active", limit=20)
            for s in services:
                if s.id not in used_service_ids and s not in recommendations:
                    recommendations.append(s)
                    if len(recommendations) >= limit:
                        return recommendations

        return recommendations[:limit]
