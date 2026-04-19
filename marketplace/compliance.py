"""
Runtime compliance enforcement — validates key compliance requirements are met.

Called during application startup to log warnings for non-compliance.
Does not block startup (graceful degradation) but logs clear warnings.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger("acf.compliance")


@dataclass(frozen=True)
class ComplianceResult:
    """Immutable result of a single compliance check."""
    check_name: str
    passed: bool
    severity: str  # "critical", "warning", "info"
    message: str


def compliance_check() -> list[ComplianceResult]:
    """Run all compliance checks and return results.

    Checks:
    1. ACF_WEBHOOK_KEY is set (required for webhook HMAC signing security)
    2. ACF_ADMIN_SECRET is set (required for admin endpoint authentication)
    3. Audit logging is enabled (AuditLogger can be initialized)
    4. CORS is not wildcard in production
    5. Rate limiting is configured
    6. Session secrets are not default/random

    Returns list of ComplianceResult. Does not raise exceptions.
    """
    results: list[ComplianceResult] = []

    # 1. Webhook key
    webhook_key = os.environ.get("ACF_WEBHOOK_KEY", "")
    if webhook_key:
        results.append(ComplianceResult(
            check_name="webhook_key",
            passed=True,
            severity="info",
            message="ACF_WEBHOOK_KEY is configured",
        ))
    else:
        results.append(ComplianceResult(
            check_name="webhook_key",
            passed=False,
            severity="warning",
            message="ACF_WEBHOOK_KEY not set — webhook signing uses fallback key (insecure for production)",
        ))

    # 2. Admin secret
    admin_secret = os.environ.get("ACF_ADMIN_SECRET", "")
    if admin_secret:
        results.append(ComplianceResult(
            check_name="admin_secret",
            passed=True,
            severity="info",
            message="ACF_ADMIN_SECRET is configured",
        ))
    else:
        results.append(ComplianceResult(
            check_name="admin_secret",
            passed=False,
            severity="critical",
            message="ACF_ADMIN_SECRET not set — admin endpoints may be inaccessible or insecure",
        ))

    # 3. Audit logging
    try:
        from marketplace.audit import AuditLogger
        db_path = os.environ.get("DATABASE_PATH")
        _audit = AuditLogger(db_path)
        results.append(ComplianceResult(
            check_name="audit_logging",
            passed=True,
            severity="info",
            message="Audit logging is operational",
        ))
    except Exception as exc:
        results.append(ComplianceResult(
            check_name="audit_logging",
            passed=False,
            severity="warning",
            message=f"Audit logging initialization failed: {exc}",
        ))

    # 4. CORS configuration
    cors_origins = os.environ.get("CORS_ORIGINS", "")
    if cors_origins and cors_origins != "*":
        results.append(ComplianceResult(
            check_name="cors_config",
            passed=True,
            severity="info",
            message=f"CORS restricted to: {cors_origins[:100]}",
        ))
    else:
        results.append(ComplianceResult(
            check_name="cors_config",
            passed=False,
            severity="warning",
            message="CORS_ORIGINS not set or wildcard — restrict for production",
        ))

    # 5. Rate limiting backend
    rl_backend = os.environ.get("RATE_LIMIT_BACKEND", "memory")
    if rl_backend == "database":
        results.append(ComplianceResult(
            check_name="rate_limiting",
            passed=True,
            severity="info",
            message="Rate limiting uses database backend (multi-worker safe)",
        ))
    else:
        results.append(ComplianceResult(
            check_name="rate_limiting",
            passed=False,
            severity="warning",
            message="Rate limiting uses in-memory backend — not shared across workers",
        ))

    # 6. Portal secret
    portal_secret = os.environ.get("ACF_PORTAL_SECRET", "")
    if portal_secret:
        results.append(ComplianceResult(
            check_name="portal_secret",
            passed=True,
            severity="info",
            message="ACF_PORTAL_SECRET is configured",
        ))
    else:
        results.append(ComplianceResult(
            check_name="portal_secret",
            passed=False,
            severity="warning",
            message="ACF_PORTAL_SECRET not set — portal sessions use random key (resets on restart)",
        ))

    return results


def log_compliance_results(results: list[ComplianceResult] | None = None) -> dict:
    """Run compliance checks (or use provided results) and log them.

    Returns a summary dict with counts and the full results list.
    """
    if results is None:
        results = compliance_check()

    passed_count = sum(1 for r in results if r.passed)
    failed_count = sum(1 for r in results if not r.passed)
    critical_count = sum(1 for r in results if not r.passed and r.severity == "critical")

    logger.info(
        "Compliance check: %d/%d passed, %d failed (%d critical)",
        passed_count, len(results), failed_count, critical_count,
    )

    for result in results:
        if result.passed:
            logger.info("  [PASS] %s: %s", result.check_name, result.message)
        elif result.severity == "critical":
            logger.error("  [FAIL] %s: %s", result.check_name, result.message)
        else:
            logger.warning("  [WARN] %s: %s", result.check_name, result.message)

    # Enforce critical compliance checks in production mode.
    # When DATABASE_URL is set (indicating PG/production), critical failures
    # raise an error to prevent the application from starting insecurely.
    is_production = bool(os.environ.get("DATABASE_URL"))
    enforce = os.environ.get("ACF_ENFORCE_COMPLIANCE", "auto")
    should_enforce = (enforce == "true") or (enforce == "auto" and is_production)

    if should_enforce and critical_count > 0:
        critical_msgs = [
            r.message for r in results
            if not r.passed and r.severity == "critical"
        ]
        logger.error(
            "COMPLIANCE ENFORCEMENT: %d critical check(s) failed — "
            "set ACF_ENFORCE_COMPLIANCE=false to override (not recommended)",
            critical_count,
        )
        for msg in critical_msgs:
            logger.error("  CRITICAL: %s", msg)
        # Block startup when enforcement is active and critical checks fail
        raise RuntimeError(
            f"Compliance enforcement blocked startup: {critical_count} critical "
            f"check(s) failed. Set ACF_ENFORCE_COMPLIANCE=false to override."
        )

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_checks": len(results),
        "passed": passed_count,
        "failed": failed_count,
        "critical_failures": critical_count,
        "enforced": should_enforce,
        "results": [
            {
                "check_name": r.check_name,
                "passed": r.passed,
                "severity": r.severity,
                "message": r.message,
            }
            for r in results
        ],
    }
