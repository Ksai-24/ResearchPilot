"""Test suite for load balancing, concurrency limiting, and multi-API switching."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aira.load_balancer import LoadBalancer


async def run_tests():
    print("== 1. Initial State ==")
    lb = LoadBalancer()
    lb.high_traffic_threshold = 2
    stats = lb.get_stats()
    assert stats["traffic_status"] == "NORMAL"
    assert stats["active_requests"] == 0
    route = lb.get_route()
    assert route["provider"] == "primary"
    print(f"  Normal traffic routed to: {route['provider']} (model: {route['model']})")

    print("\n== 2. High Concurrency Traffic Switching ==")
    # Simulate concurrency >= threshold
    async with lb.acquire_slot():
        assert lb.get_stats()["active_requests"] == 1
        async with lb.acquire_slot():
            assert lb.get_stats()["active_requests"] == 2
            # Now active_requests is 2 >= threshold (2) -> must switch to secondary!
            stats_high = lb.get_stats()
            assert stats_high["traffic_status"] == "HIGH", "Must detect high traffic state"
            route_high = lb.get_route()
            assert route_high["provider"] == "secondary", "Must switch to secondary provider during high traffic"
            print(f"  High traffic automatically switched to: {route_high['provider']} (model: {route_high['model']})")

    # Concurrency released -> back to normal
    stats_after = lb.get_stats()
    assert stats_after["active_requests"] == 0
    assert stats_after["traffic_status"] == "NORMAL"
    route_after = lb.get_route()
    assert route_after["provider"] == "primary"
    print(f"  Traffic restored to: {route_after['provider']}")

    print("\n== 3. Rate-Limit / 429 Failover Trigger ==")
    lb.report_rate_limit("primary", cooldown_seconds=10)
    stats_rl = lb.get_stats()
    assert stats_rl["traffic_status"] == "HIGH"
    route_rl = lb.get_route()
    assert route_rl["provider"] == "secondary"
    print(f"  Rate-limit failover successfully routed to: {route_rl['provider']} ({route_rl['reason']})")

    print("\nALL LOAD BALANCER TESTS PASSED!")


if __name__ == "__main__":
    asyncio.run(run_tests())
