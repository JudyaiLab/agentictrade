"""
Legal endpoints — Privacy Policy, Terms of Service, and GDPR data rights.

Static endpoints that return placeholder legal text.
No authentication required for read endpoints. Content should be customized
for production.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/legal", tags=["legal"])


@router.get("/versions")
async def legal_versions():
    """Return the current version of each legal document and effective dates."""
    return {
        "privacy_policy": {"current_version": "1.0.0", "effective_date": "2026-01-01"},
        "terms_of_service": {"current_version": "1.0.0", "effective_date": "2026-01-01"},
    }


@router.delete("/data-deletion/{user_id}")
async def request_data_deletion(user_id: str, request: Request):
    """GDPR right-to-erasure: delete all personal data for a user.

    Requires admin authentication via Bearer token.
    """
    from api.deps import require_admin
    require_admin(request)
    db = request.app.state.db
    result = db.delete_user_data(user_id)
    # Log the deletion event in audit trail if available
    audit = getattr(request.app.state, "audit", None)
    if audit:
        audit.log_event(
            "admin_action",
            "system",
            target=user_id,
            details=f"Data deletion: {result}",
        )
    return {"status": "completed", "user_id": user_id, "deleted": result}


@router.get("/privacy")
async def privacy_policy():
    """Return the platform privacy policy.

    This is placeholder content. Customize for your jurisdiction and use case
    before deploying to production.
    """
    return {
        "title": "Privacy Policy",
        "version": "1.0.0",
        "effective_date": "2026-01-01",
        "last_updated": "2026-03-25",
        "notice": (
            "This is placeholder privacy policy content. "
            "You must customize this document for your specific jurisdiction, "
            "business requirements, and applicable regulations (e.g., GDPR, CCPA) "
            "before deploying to production."
        ),
        "sections": [
            {
                "heading": "1. Information We Collect",
                "content": (
                    "We collect information you provide directly: email address, "
                    "display name, company name, and API usage data. We also collect "
                    "technical data: IP addresses, request timestamps, and API call metadata."
                ),
            },
            {
                "heading": "2. How We Use Your Information",
                "content": (
                    "We use collected information to: operate the marketplace platform, "
                    "process transactions and settlements, send service notifications, "
                    "monitor platform health and security, and improve our services."
                ),
            },
            {
                "heading": "3. Data Sharing",
                "content": (
                    "We do not sell personal data. We share data only as needed to: "
                    "process payments (with payment providers), comply with legal obligations, "
                    "and protect the security of the platform."
                ),
            },
            {
                "heading": "4. Data Retention",
                "content": (
                    "Transaction records are retained for the duration required by applicable "
                    "financial regulations. Account data is retained while your account is active "
                    "and for a reasonable period thereafter."
                ),
            },
            {
                "heading": "5. Your Rights",
                "content": (
                    "Depending on your jurisdiction, you may have rights to: access your data, "
                    "correct inaccurate data, delete your data, export your data, "
                    "and opt out of marketing communications."
                ),
            },
            {
                "heading": "6. Contact",
                "content": (
                    "For privacy-related inquiries, contact us at privacy@agentictrade.io."
                ),
            },
        ],
    }


@router.get("/terms")
async def terms_of_service():
    """Return the platform terms of service.

    This is placeholder content. Customize for your jurisdiction and use case
    before deploying to production.
    """
    return {
        "title": "Terms of Service",
        "version": "1.0.0",
        "effective_date": "2026-01-01",
        "last_updated": "2026-03-25",
        "notice": (
            "This is placeholder terms of service content. "
            "You must customize this document for your specific jurisdiction, "
            "business requirements, and applicable regulations "
            "before deploying to production."
        ),
        "sections": [
            {
                "heading": "1. Acceptance of Terms",
                "content": (
                    "By accessing or using the AgenticTrade platform, you agree to be "
                    "bound by these Terms of Service. If you do not agree, do not use "
                    "the platform."
                ),
            },
            {
                "heading": "2. Platform Description",
                "content": (
                    "AgenticTrade is an API marketplace where AI agents discover, call, "
                    "and pay for services. The platform facilitates transactions between "
                    "service providers and service consumers (buyers/agents)."
                ),
            },
            {
                "heading": "3. Account Responsibilities",
                "content": (
                    "You are responsible for maintaining the security of your API keys "
                    "and account credentials. You must not share credentials or use the "
                    "platform for illegal purposes."
                ),
            },
            {
                "heading": "4. Fees and Payments",
                "content": (
                    "The platform charges a commission on transactions as described in "
                    "the pricing page. Commission rates: 0% (month 1), 5% (months 2-3), "
                    "10% (month 4+). Settlements are processed according to the settlement "
                    "schedule."
                ),
            },
            {
                "heading": "5. Service Level",
                "content": (
                    "We strive to maintain platform availability but do not guarantee "
                    "uninterrupted service. Service providers are responsible for their "
                    "own API uptime and quality."
                ),
            },
            {
                "heading": "6. Limitation of Liability",
                "content": (
                    "The platform is provided 'as is'. We are not liable for damages "
                    "arising from service provider outages, payment processing delays, "
                    "or other issues beyond our reasonable control."
                ),
            },
            {
                "heading": "7. Modifications",
                "content": (
                    "We may update these terms at any time. Continued use of the platform "
                    "after changes constitutes acceptance of the updated terms."
                ),
            },
        ],
    }
