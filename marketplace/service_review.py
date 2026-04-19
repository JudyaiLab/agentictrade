"""
Automated Service Review Pipeline for Agent Provider listings.

Runs security-first checks on every new service endpoint before it goes live.
Designed to be called by cron (batch) or on-demand via the provider portal API.

Security is the top priority: endpoints are tested with redirects disabled,
response bodies are size-limited, and suspicious headers/dispositions are flagged.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
import time
import uuid
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import httpx

from .db import Database

logger = logging.getLogger("service_review")

# Domains that are never acceptable redirect targets.
_SUSPICIOUS_DOMAINS: frozenset[str] = frozenset((
    "bit.ly", "tinyurl.com", "t.co", "goo.gl", "rb.gy",
    "is.gd", "v.gd", "shorturl.at", "cutt.ly",
    "localhost", "0.0.0.0", "127.0.0.1", "[::1]",
    "metadata.google.internal",
))

# Content-Disposition values that indicate executable / attachment delivery.
_DANGEROUS_DISPOSITIONS: frozenset[str] = frozenset((
    "attachment",
))

# Headers whose mere presence is suspicious on a JSON API.
_SUSPICIOUS_HEADERS: frozenset[str] = frozenset((
    "x-malware", "x-miner", "x-coinhive",
))


def _is_private_ip(hostname: str) -> bool:
    """Check if a hostname resolves to a private, loopback, or link-local IP.

    Performs DNS resolution to catch cases like internal.company.com -> 10.0.0.1.
    Returns True if the address is non-routable (should be blocked for SSRF).
    """
    try:
        # Try parsing as IP literal first
        addr = ipaddress.ip_address(hostname)
        return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved
    except ValueError:
        pass

    # Resolve hostname to IP
    try:
        infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for info in infos:
            ip_str = info[4][0]
            addr = ipaddress.ip_address(ip_str)
            if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
                return True
    except (socket.gaierror, ValueError, OSError):
        pass

    return False


class ServiceReviewEngine:
    """Automated review pipeline for new service listings.

    All checks must pass for a service to go live:
    1. Endpoint reachable (HTTP GET returns < 500)
    2. Response format valid (Content-Type includes json)
    3. Response time acceptable (< 10 s)
    4. No malicious indicators
    """

    MAX_RESPONSE_TIME_MS: int = 10_000
    MAX_REDIRECTS: int = 3
    MAX_RESPONSE_BYTES: int = 10 * 1024 * 1024  # 10 MB

    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_review(
        self,
        service_id: str,
        provider_id: str,
    ) -> dict:
        """Create a pending review record and return it as a dict."""
        now = datetime.now(timezone.utc).isoformat()
        review: dict = {
            "id": str(uuid.uuid4()),
            "service_id": service_id,
            "provider_id": provider_id,
            "review_type": "automated",
            "status": "pending",
            "endpoint_reachable": 0,
            "response_format_valid": 0,
            "response_time_ms": 0,
            "malicious_check_passed": 0,
            "error_details": "",
            "reviewer_notes": "",
            "reviewed_at": None,
            "created_at": now,
        }
        self.db.insert_service_review(review)
        logger.info(
            "Created pending review %s for service %s",
            review["id"], service_id,
        )
        return review

    async def execute_review(self, review_id: str) -> dict:
        """Run all automated checks against the service endpoint.

        Updates the review record with results.  If every check passes the
        service status is moved to ``'active'``; otherwise it stays as
        ``'under_review'``.

        Returns the updated review dict.
        """
        review = self.db.get_service_review(review_id)
        if review is None:
            raise ValueError(f"Review {review_id} not found")

        service = self.db.get_service(review["service_id"])
        if service is None:
            return self._fail_review(
                review_id, "Service not found in database",
            )

        endpoint: str = service["endpoint"]
        if not endpoint:
            return self._fail_review(review_id, "Service endpoint is empty")

        # Validate endpoint URL scheme before making any request.
        parsed = urlparse(endpoint)
        if parsed.scheme not in ("http", "https"):
            return self._fail_review(
                review_id, f"Invalid URL scheme: {parsed.scheme}",
            )

        # SSRF protection: block private/internal IP targets
        hostname = (parsed.hostname or "").lower()
        if hostname in _SUSPICIOUS_DOMAINS:
            return self._fail_review(
                review_id, f"Blocked domain: {hostname}",
            )
        if _is_private_ip(hostname):
            logger.warning("SSRF blocked: %s resolves to private IP", hostname)
            return self._fail_review(
                review_id, f"SSRF blocked: {hostname} resolves to a private/internal IP",
            )

        # --- Check 1: Endpoint reachability --------------------------
        reachable, status_code, latency_ms, response = (
            await self._check_endpoint(endpoint)
        )

        # --- Check 2: Response format --------------------------------
        format_valid = False
        if response is not None:
            format_valid = self._check_response_format(response)

        # --- Check 3: Response time ----------------------------------
        time_ok = latency_ms <= self.MAX_RESPONSE_TIME_MS

        # --- Check 4: Malicious indicators ---------------------------
        malicious_passed = True
        malicious_notes = ""
        if response is not None:
            malicious_passed, malicious_notes = self._check_malicious(
                endpoint, response,
            )

        # --- Determine overall result --------------------------------
        all_passed = (
            reachable and format_valid and time_ok and malicious_passed
        )

        error_parts: list[str] = []
        if not reachable:
            error_parts.append(f"endpoint_unreachable(status={status_code})")
        if not format_valid:
            error_parts.append("response_not_json")
        if not time_ok:
            error_parts.append(f"too_slow({latency_ms}ms)")
        if not malicious_passed:
            error_parts.append(f"malicious({malicious_notes})")

        now = datetime.now(timezone.utc).isoformat()

        updates: dict = {
            "endpoint_reachable": 1 if reachable else 0,
            "response_format_valid": 1 if format_valid else 0,
            "response_time_ms": latency_ms,
            "malicious_check_passed": 1 if malicious_passed else 0,
            "error_details": "; ".join(error_parts),
            "reviewer_notes": malicious_notes if malicious_notes else "",
            "reviewed_at": now,
            "status": "passed" if all_passed else "failed",
        }
        self.db.update_service_review(review_id, updates)

        # Promote service to active when all checks pass.
        if all_passed:
            self.db.update_service(
                review["service_id"],
                {"status": "active", "updated_at": now},
            )
            logger.info(
                "Review %s PASSED — service %s is now active",
                review_id, review["service_id"],
            )
        else:
            # Keep the service under review so it cannot receive traffic.
            self.db.update_service(
                review["service_id"],
                {"status": "under_review", "updated_at": now},
            )
            logger.warning(
                "Review %s FAILED — service %s stays under_review: %s",
                review_id, review["service_id"], "; ".join(error_parts),
            )

        return {**review, **updates}

    def should_skip_review(self, provider_id: str) -> bool:
        """Check fast-track eligibility.

        A provider can skip automated review if their ``fast_track_eligible``
        flag is set (e.g. Founding Seller with a clean track record).
        """
        provider = self.db.get_agent_provider(provider_id)
        if provider is None:
            return False
        return bool(provider.get("fast_track_eligible", False))

    async def review_pending_services(self) -> list[dict]:
        """Batch-review all pending service reviews.  Intended for cron."""
        pending = self.db.list_pending_reviews(limit=100)
        if not pending:
            logger.info("No pending reviews to process")
            return []

        logger.info("Processing %d pending reviews", len(pending))
        results: list[dict] = []
        for review in pending:
            try:
                result = await self.execute_review(review["id"])
                results.append(result)
            except Exception as exc:
                logger.error(
                    "Failed to execute review %s: %s", review["id"], exc,
                )
                self._fail_review(review["id"], f"exception: {type(exc).__name__}")
        return results

    # ------------------------------------------------------------------
    # Internal checks
    # ------------------------------------------------------------------

    async def _check_endpoint(
        self,
        endpoint: str,
    ) -> tuple[bool, int, int, Optional[httpx.Response]]:
        """Test endpoint reachability.

        Returns ``(reachable, status_code, latency_ms, response)``.
        ``response`` is ``None`` when the request failed entirely.

        Redirects are followed manually up to ``MAX_REDIRECTS`` so we can
        inspect each hop for suspicious domains.
        """
        status_code = 0
        latency_ms = 0
        response: Optional[httpx.Response] = None
        current_url = endpoint
        redirects_followed = 0

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.MAX_RESPONSE_TIME_MS / 1000),
                follow_redirects=False,
                max_redirects=0,
            ) as client:
                start = time.monotonic()

                while True:
                    resp = await client.get(
                        current_url,
                        headers={"User-Agent": "AgentCommerce-ReviewBot/1.0"},
                    )
                    latency_ms = round((time.monotonic() - start) * 1000)
                    status_code = resp.status_code

                    # Handle redirects manually for security inspection.
                    if resp.is_redirect and redirects_followed < self.MAX_REDIRECTS:
                        location = resp.headers.get("location", "")
                        if not location:
                            break

                        # Resolve relative redirects.
                        if location.startswith("/"):
                            parsed = urlparse(current_url)
                            location = f"{parsed.scheme}://{parsed.netloc}{location}"

                        redirect_host = urlparse(location).hostname or ""
                        if redirect_host.lower() in _SUSPICIOUS_DOMAINS:
                            logger.warning(
                                "Redirect to suspicious domain blocked: %s",
                                redirect_host,
                            )
                            return (False, status_code, latency_ms, None)

                        # SSRF: block redirect to private IPs
                        if _is_private_ip(redirect_host):
                            logger.warning(
                                "SSRF: redirect to private IP blocked: %s",
                                redirect_host,
                            )
                            return (False, status_code, latency_ms, None)

                        # Block redirects that downgrade to non-HTTPS.
                        redirect_scheme = urlparse(location).scheme
                        if redirect_scheme not in ("http", "https"):
                            return (False, status_code, latency_ms, None)

                        current_url = location
                        redirects_followed += 1
                        continue

                    # Too many redirects.
                    if resp.is_redirect:
                        logger.warning(
                            "Too many redirects (%d) for %s",
                            redirects_followed, endpoint,
                        )
                        return (False, status_code, latency_ms, None)

                    response = resp
                    break

        except httpx.TimeoutException:
            latency_ms = self.MAX_RESPONSE_TIME_MS
            logger.info("Endpoint timed out: %s", endpoint)
            return (False, 0, latency_ms, None)
        except httpx.ConnectError:
            logger.info("Connection refused: %s", endpoint)
            return (False, 0, 0, None)
        except Exception as exc:
            logger.warning("Endpoint check error for %s: %s", endpoint, exc)
            return (False, 0, 0, None)

        reachable = status_code < 500
        return (reachable, status_code, latency_ms, response)

    @staticmethod
    def _check_response_format(response: httpx.Response) -> bool:
        """Verify Content-Type includes ``json``."""
        content_type = response.headers.get("content-type", "")
        return "json" in content_type.lower()

    def _check_malicious(
        self,
        endpoint: str,
        response: httpx.Response,
    ) -> tuple[bool, str]:
        """Inspect the response for malicious indicators.

        Returns ``(passed, notes)`` where *passed* is ``True`` when no
        issues are found.

        Checks:
        - No redirect to suspicious domains (already enforced in
          ``_check_endpoint``, but double-checked via response URL).
        - No executable Content-Disposition.
        - Response body size within ``MAX_RESPONSE_BYTES``.
        - No suspicious headers.
        """
        issues: list[str] = []

        # 1. Final URL domain check (in case of transparent proxy rewrite).
        final_host = urlparse(str(response.url)).hostname or ""
        if final_host.lower() in _SUSPICIOUS_DOMAINS:
            issues.append(f"suspicious_final_domain:{final_host}")

        # 2. Content-Disposition.
        disposition = response.headers.get("content-disposition", "").lower()
        for dangerous in _DANGEROUS_DISPOSITIONS:
            if dangerous in disposition:
                issues.append(f"dangerous_disposition:{disposition}")
                break

        # 3. Response body size.
        content_length_header = response.headers.get("content-length")
        if content_length_header is not None:
            try:
                if int(content_length_header) > self.MAX_RESPONSE_BYTES:
                    issues.append(
                        f"body_too_large:{content_length_header}bytes"
                    )
            except ValueError:
                issues.append("invalid_content_length_header")
        # Also check the actual content we received.
        if len(response.content) > self.MAX_RESPONSE_BYTES:
            issues.append(
                f"body_too_large_actual:{len(response.content)}bytes"
            )

        # 4. Suspicious headers.
        for header in _SUSPICIOUS_HEADERS:
            if header in response.headers:
                issues.append(f"suspicious_header:{header}")

        passed = len(issues) == 0
        notes = "; ".join(issues) if issues else ""
        return (passed, notes)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fail_review(self, review_id: str, reason: str) -> dict:
        """Mark a review as failed with the given reason and return it."""
        now = datetime.now(timezone.utc).isoformat()
        updates: dict = {
            "status": "failed",
            "error_details": reason,
            "reviewed_at": now,
        }
        self.db.update_service_review(review_id, updates)
        logger.warning("Review %s force-failed: %s", review_id, reason)

        review = self.db.get_service_review(review_id)
        return review if review is not None else {"id": review_id, **updates}
