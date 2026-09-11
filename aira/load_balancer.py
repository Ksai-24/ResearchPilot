"""Load Balancer, Concurrency Limiter, and Multi-API High-Traffic Manager.

Manages:
1. Concurrency limiting and queueing via asyncio.Semaphore to protect system memory and rate limits.
2. Real-time traffic monitoring (in-flight concurrency and rolling requests-per-minute).
3. Automatic API switching: routes to Secondary API when traffic is high or when the Primary API hits rate limits.
"""
import asyncio
import logging
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional, Tuple

from openai import AsyncOpenAI

from . import config

logger = logging.getLogger("aira.load_balancer")


class LoadBalancer:
    """Coordinates request concurrency and multi-provider load routing."""

    def __init__(self):
        self.max_concurrency = config.MAX_CONCURRENT_REQUESTS
        self.high_traffic_threshold = config.HIGH_TRAFFIC_CONCURRENCY
        self.high_traffic_rpm = config.HIGH_TRAFFIC_RPM

        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        self._active_requests = 0
        self._waiting_requests = 0
        self._total_served = 0
        self._request_window = deque()  # timestamps of requests in last 60 seconds
        self._lock = asyncio.Lock()
        self._round_robin_counter = 0

        # Clients
        self._primary_client: Optional[AsyncOpenAI] = None
        self._primary_key_cached: str = ""
        self._primary_url_cached: str = ""

        self._secondary_client: Optional[AsyncOpenAI] = None
        self._secondary_key_cached: str = ""
        self._secondary_url_cached: str = ""

        # Primary cooldown state on 429/rate-limit
        self._primary_rate_limited_until: float = 0.0

    def _get_primary_client(self) -> AsyncOpenAI:
        if (
            self._primary_client is None
            or self._primary_key_cached != config.API_KEY
            or self._primary_url_cached != config.BASE_URL
        ):
            if not config.API_KEY:
                raise RuntimeError(
                    "AIRA_API_KEY is not set in .env. Please set a valid API key (OpenRouter, Google Gemini, Groq, or OpenAI)."
                )
            self._primary_key_cached = config.API_KEY
            self._primary_url_cached = config.BASE_URL
            self._primary_client = AsyncOpenAI(
                api_key=config.API_KEY,
                base_url=config.BASE_URL,
                timeout=config.REQUEST_TIMEOUT,
            )
        return self._primary_client

    def _get_secondary_client(self) -> AsyncOpenAI:
        sec_key = config.SECONDARY_API_KEY or config.API_KEY
        sec_url = config.SECONDARY_BASE_URL or config.BASE_URL
        if (
            self._secondary_client is None
            or self._secondary_key_cached != sec_key
            or self._secondary_url_cached != sec_url
        ):
            if not sec_key:
                raise RuntimeError(
                    "Neither AIRA_SECONDARY_API_KEY nor AIRA_API_KEY is set in .env."
                )
            self._secondary_key_cached = sec_key
            self._secondary_url_cached = sec_url
            self._secondary_client = AsyncOpenAI(
                api_key=sec_key,
                base_url=sec_url,
                timeout=config.REQUEST_TIMEOUT,
            )
        return self._secondary_client

    def _clean_window(self, now: float) -> int:
        cutoff = now - 60.0
        while self._request_window and self._request_window[0] < cutoff:
            self._request_window.popleft()
        return len(self._request_window)

    def is_high_traffic(self) -> Tuple[bool, str]:
        """Check if system is currently experiencing high traffic."""
        now = time.time()
        rpm = self._clean_window(now)

        if now < self._primary_rate_limited_until:
            remaining = int(self._primary_rate_limited_until - now)
            return True, f"Primary API rate-limited (cooling down for {remaining}s)"

        if self._active_requests >= self.high_traffic_threshold:
            return True, f"High concurrency ({self._active_requests} in-flight >= threshold {self.high_traffic_threshold})"

        if rpm >= self.high_traffic_rpm:
            return True, f"High request rate ({rpm} RPM >= threshold {self.high_traffic_rpm})"

        return False, "Normal"

    def report_rate_limit(self, provider: str = "primary", cooldown_seconds: int = 45):
        """Mark primary provider as rate-limited, switching routing to secondary immediately."""
        if provider == "primary":
            self._primary_rate_limited_until = time.time() + cooldown_seconds
            logger.warning("Primary API rate limit reported. Auto-routing to Secondary API for %ds.", cooldown_seconds)

    def get_route(self, force_secondary: bool = False) -> Dict[str, Any]:
        """Select active API provider with round-robin dual-pipe balancing under high traffic."""
        now = time.time()
        high_traffic, reason = self.is_high_traffic()

        if force_secondary:
            pick_secondary = True
        elif now < self._primary_rate_limited_until:
            pick_secondary = True
        elif high_traffic:
            # Under high load (50 users), balance requests 50/50 across Primary and Secondary providers
            self._round_robin_counter += 1
            pick_secondary = (self._round_robin_counter % 2 == 1)
        else:
            pick_secondary = False

        if pick_secondary:
            try:
                client = self._get_secondary_client()
                return {
                    "client": client,
                    "model": config.SECONDARY_MODEL,
                    "provider": "secondary",
                    "reason": reason if high_traffic else "Balanced route to secondary provider",
                    "is_high_traffic": high_traffic,
                }
            except Exception as e:
                logger.warning("Failed to initialize secondary client (%s), falling back to primary", e)

        client = self._get_primary_client()
        return {
            "client": client,
            "model": config.MODEL,
            "provider": "primary",
            "reason": reason if high_traffic else "Normal traffic routed to primary",
            "is_high_traffic": high_traffic,
        }

    @asynccontextmanager
    async def acquire_slot(self, timeout: float = 60.0):
        """Acquire an execution slot under concurrency limit to protect server memory and rate limits."""
        now = time.time()
        async with self._lock:
            self._clean_window(now)
            self._request_window.append(now)
            self._waiting_requests += 1

        try:
            acquired = False
            try:
                await asyncio.wait_for(self._semaphore.acquire(), timeout=timeout)
                acquired = True
            except asyncio.TimeoutError:
                raise RuntimeError(
                    f"Server is experiencing high traffic ({self._active_requests} requests executing, {self._waiting_requests} waiting). "
                    "Please retry in a moment."
                )

            async with self._lock:
                self._waiting_requests = max(0, self._waiting_requests - 1)
                self._active_requests += 1
                self._total_served += 1

            yield {"active": self._active_requests, "waiting": self._waiting_requests}

        finally:
            if acquired:
                self._semaphore.release()
                async with self._lock:
                    self._active_requests = max(0, self._active_requests - 1)

    def get_stats(self) -> Dict[str, Any]:
        """Return real-time load and traffic diagnostics."""
        now = time.time()
        rpm = self._clean_window(now)
        high_traffic, reason = self.is_high_traffic()

        return {
            "active_requests": self._active_requests,
            "waiting_queue": self._waiting_requests,
            "max_concurrency": self.max_concurrency,
            "rpm": rpm,
            "high_traffic": high_traffic,
            "traffic_status": "HIGH" if high_traffic else "NORMAL",
            "traffic_reason": reason,
            "current_provider": "secondary" if high_traffic else "primary",
            "primary_model": config.MODEL,
            "secondary_model": config.SECONDARY_MODEL,
            "total_requests_served": self._total_served,
        }


# Singleton instance
load_balancer = LoadBalancer()
