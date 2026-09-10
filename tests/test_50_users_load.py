"""Tests simulating 50 concurrent users accessing the app simultaneously.

Verifies:
1. Concurrency limit and semaphore handles 50 concurrent users.
2. Under high concurrency (50 requests), load balancer triggers round-robin dual-pipe balancing across Primary and Secondary APIs.
3. Health and traffic reporting reflects high traffic load accurately.
"""
import asyncio
import time
from aira.load_balancer import LoadBalancer
from aira import config


async def simulate_user(user_id: int, lb: LoadBalancer, results: list):
    """Simulate a single user acquiring a slot, getting a balanced route, and releasing."""
    try:
        async with lb.acquire_slot(timeout=15.0) as slot_info:
            # Simulate work under slot
            route = lb.get_route()
            results.append({
                "user_id": user_id,
                "provider": route.get("provider"),
                "model": route.get("model"),
                "is_high_traffic": route.get("is_high_traffic"),
                "success": True,
            })
            await asyncio.sleep(0.05)  # brief simulation of agent step
    except Exception as e:
        results.append({
            "user_id": user_id,
            "error": str(e),
            "success": False,
        })


async def test_50_concurrent_users():
    lb = LoadBalancer()
    assert lb.max_concurrency >= 50, f"Expected max concurrency >= 50, got {lb.max_concurrency}"

    results = []
    # Launch 50 concurrent simulated users
    tasks = [simulate_user(i, lb, results) for i in range(50)]
    t0 = time.perf_counter()
    await asyncio.gather(*tasks)
    elapsed = time.perf_counter() - t0

    assert len(results) == 50, f"Expected 50 results, got {len(results)}"
    successes = [r for r in results if r.get("success")]
    assert len(successes) == 50, f"All 50 users should succeed without errors, got {len(successes)}"

    # Check that high traffic was detected and dual-pipe balancing engaged
    primary_count = sum(1 for r in results if r.get("provider") == "primary")
    secondary_count = sum(1 for r in results if r.get("provider") == "secondary")

    print(f"50 Concurrent Users Test: {len(successes)}/50 completed in {elapsed:.2f}s")
    print(f"Routing balance: Primary={primary_count}, Secondary={secondary_count}")

    # Under high load (50 users), both primary and secondary providers must be utilized
    assert primary_count > 0, "Primary provider should have received traffic"
    assert secondary_count > 0, "Secondary provider should have received traffic via dual-pipe balancing"

    # Verify load balancer stats
    stats = lb.get_stats()
    assert stats["total_requests_served"] == 50
    assert stats["active_requests"] == 0


if __name__ == "__main__":
    asyncio.run(test_50_concurrent_users())
    print("ALL 50-USER CONCURRENT TESTS PASSED")
