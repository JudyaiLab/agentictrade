"""Ad serving engine for AdCP integration.

Selects matching campaigns for a given context and returns sponsored results
with proper disclosure. Automatically logs impressions.
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("adcp.serving")


def get_sponsored_results(
    campaigns: dict[str, dict],
    context: str,
    category: Optional[str] = None,
    limit: int = 1,
) -> list[dict]:
    """Return sponsored results matching the given context.

    Args:
        campaigns: The in-memory campaign store from adcp route.
        context: Where the ad will appear (api_search, category_page, etc.).
        category: Optional category filter for targeting.
        limit: Maximum number of sponsored results.

    Returns:
        List of sponsored result dicts, each with is_sponsored=True.
    """
    from api.routes.adcp import AD_PRODUCTS, _campaigns, _impressions
    import time

    matches: list[dict] = []

    for campaign_id, campaign in _campaigns.items():
        if campaign["status"] != "active":
            continue

        # Check budget exhaustion
        if campaign["budget_spent"] >= campaign["budget"]:
            continue

        # Find the product definition for targeting
        product = next(
            (p for p in AD_PRODUCTS if p["product_id"] == campaign["product_id"]),
            None,
        )
        if not product:
            continue

        targeting = product.get("targeting", {})
        contexts = targeting.get("contexts", [])
        categories = targeting.get("categories", [])

        # Match context
        context_match = context in contexts or "all" in contexts
        # Match category
        category_match = (
            not category
            or "all" in categories
            or category.lower() in [c.lower() for c in categories]
        )

        if context_match and category_match:
            matches.append({
                "campaign_id": campaign_id,
                "campaign": campaign,
                "product": product,
            })

        if len(matches) >= limit:
            break

    # Build sponsored results and log impressions
    results = []
    for m in matches:
        campaign = m["campaign"]
        product = m["product"]

        # Log impression
        campaign["impressions"] += 1
        pricing = campaign["pricing"]
        if pricing["model"] == "cpm":
            campaign["budget_spent"] += pricing["rate"] / 1000

        _impressions.append({
            "campaign_id": m["campaign_id"],
            "ts": time.time(),
            "context": context,
        })

        results.append({
            "is_sponsored": True,
            "sponsored_by": campaign["brand_domain"],
            "creative_text": campaign["creative_text"],
            "landing_url": campaign["landing_url"],
            "campaign_id": m["campaign_id"],
            "product_type": product["product_id"],
            "disclosure": "Sponsored",
        })

        log.info(
            "Ad served: campaign=%s brand=%s context=%s",
            m["campaign_id"], campaign["brand_domain"], context,
        )

    return results


def inject_into_service_list(
    services: list[dict],
    context: str,
    category: Optional[str] = None,
    max_ads: int = 1,
    position: int = 0,
) -> list[dict]:
    """Inject sponsored results into a service listing.

    Inserts sponsored entries at the specified position (default: top).
    Each sponsored entry is clearly marked with is_sponsored=True.

    Args:
        services: Original service list (dicts).
        context: Ad context for targeting.
        category: Optional category for targeting.
        max_ads: Maximum sponsored results to inject.
        position: Index to insert at (0=top).

    Returns:
        New list with sponsored results injected (original unchanged).
    """
    sponsored = get_sponsored_results(
        campaigns={},  # Not used — function reads from adcp module directly
        context=context,
        category=category,
        limit=max_ads,
    )

    if not sponsored:
        return services

    # Build sponsored service entries
    result = list(services)  # Copy — don't mutate
    for i, ad in enumerate(sponsored):
        sponsored_entry = {
            "id": f"sponsored_{ad['campaign_id']}",
            "name": ad["creative_text"] or f"Sponsored: {ad['sponsored_by']}",
            "description": ad["creative_text"],
            "is_sponsored": True,
            "disclosure": "Sponsored",
            "sponsored_by": ad["sponsored_by"],
            "landing_url": ad["landing_url"],
            "campaign_id": ad["campaign_id"],
        }
        insert_at = min(position + i, len(result))
        result.insert(insert_at, sponsored_entry)

    return result
