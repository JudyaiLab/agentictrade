"""
Payment Router — routes payment requests to the appropriate provider.

Provides a single entry point for resolving which PaymentProvider
handles a given payment method string.
"""
from __future__ import annotations

import logging
from typing import Optional

from .base import PaymentProvider

logger = logging.getLogger("payments.router")


class PaymentRouter:
    """
    Routes payment method strings to PaymentProvider instances.

    Example usage:
        router = PaymentRouter({
            "x402": x402_provider,
            "nowpayments": nowpayments_provider,
        })
        provider = router.route("x402")
        if provider:
            result = await provider.create_payment(...)
    """

    def __init__(self, providers: dict[str, PaymentProvider]):
        """
        Initialize the router with a mapping of method names to providers.

        Args:
            providers: Dict mapping payment method names to PaymentProvider instances.
                       Keys are lowercased for case-insensitive lookup.
        """
        self._providers: dict[str, PaymentProvider] = {
            key.lower(): provider for key, provider in providers.items()
        }
        logger.info(
            "PaymentRouter initialized with %d providers: %s",
            len(self._providers),
            list(self._providers.keys()),
        )

    def route(self, payment_method: str) -> Optional[PaymentProvider]:
        """
        Resolve a payment method string to a PaymentProvider.

        Args:
            payment_method: The requested payment method (e.g. "x402", "nowpayments").

        Returns:
            The matching PaymentProvider, or None if not found.
        """
        if not payment_method:
            logger.warning("Empty payment_method requested")
            return None

        provider = self._providers.get(payment_method.lower())
        if provider is None:
            logger.warning(
                "No provider found for payment_method='%s'. Available: %s",
                payment_method,
                list(self._providers.keys()),
            )
        return provider

    def list_providers(self) -> list[str]:
        """
        List all registered payment method names.

        Returns:
            Sorted list of available payment method strings.
        """
        return sorted(self._providers.keys())

    def get_provider(self, name: str) -> Optional[PaymentProvider]:
        """
        Get a specific provider by name. Alias for route().

        Args:
            name: Provider name.

        Returns:
            PaymentProvider or None.
        """
        return self.route(name)

    def __len__(self) -> int:
        return len(self._providers)

    def __contains__(self, payment_method: str) -> bool:
        return payment_method.lower() in self._providers
