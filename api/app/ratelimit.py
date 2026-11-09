"""Tiny in-memory sliding-window rate limiter for the public booking endpoints.
One process only: fine for this demo's single always-on API instance; use Redis if it is ever scaled out."""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Request

from .config import get_settings


class RateLimiter:
    def __init__(self, rules: list[tuple[int, int]]):
        """rules = [(max_requests, per_seconds), ...]; ALL must pass."""
        self.rules = rules
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, now: float | None = None) -> int | None:
        """Record a hit. Returns None if allowed, else the number of seconds until the caller may retry."""
        now = time.monotonic() if now is None else now
        q = self.hits[key]
        horizon = max(per for _, per in self.rules)
        while q and now - q[0] > horizon:
            q.popleft()
        for limit, per in self.rules:
            recent = [t for t in q if now - t <= per]
            if len(recent) >= limit:
                return max(1, int(per - (now - recent[0])) + 1)
        q.append(now)
        return None

    def reset(self) -> None:
        self.hits.clear()


# a person books once, maybe twice: 5 a minute and 30 an hour per address is generous for people, hostile to scripts
booking_limiter = RateLimiter([(5, 60), (30, 3600)])
slots_limiter = RateLimiter([(60, 60)])


def client_ip(request: Request) -> str:
    """The caller's address. Behind N trusted proxies the real client is the Nth entry from the right of
    X-Forwarded-For (TRUSTED_PROXY_HOPS); never trust the header when no proxy is configured (it is spoofable)."""
    hops = get_settings().trusted_proxy_hops
    if hops > 0:
        parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if len(parts) >= hops:
            return parts[-hops]
    return request.client.host if request.client else "unknown"
