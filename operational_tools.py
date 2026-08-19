"""
Phase 1 — Complete Hermes Operational Visibility (2026-08-04).

Main WhatsApp bridge (bridge1/bridge2/bridge3) message monitoring, DLQ
depth, bridge-specific health/lag diagnostics, and system CPU/RAM/disk
metrics. Deliberately separate from metrics_tools.py, which covers
social_auto_reply's external Meta/Facebook channel only — confirmed
architecturally distinct from the main internal bridges (see the gap
analysis, 2026-08-04: "Keep existing social/Meta monitoring separate from
main WhatsApp bridge monitoring").

Every tool here reuses an existing fazle-core endpoint, all read-only,
all already admin-API-key-gated:
  - GET /api/whatsapp/message-stats  (NEW endpoint added 2026-08-04,
    narrow/purpose-built — reuses the same schema-aware timestamp helper
    /admin/overview already used, does not duplicate that endpoint)
  - GET /api/queue/dead-letters      (already existed, unmodified)
  - GET /api/bridges/diagnostics     (already existed, unmodified)
  - GET /health                      (already existed; gained a CPU probe
    2026-08-04 alongside its existing disk/mem probes — no new endpoint)
"""

import fazle_core_client as core


def _clamp(value, default, maximum):
    # Mirrors metrics_tools._clamp_limit — deliberately duplicated rather
    # than imported (see fazle_core_client.py / architecture notes: each
    # tool module stays independently safe against bad LLM-supplied input).
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if n <= 0:
        return default
    return min(n, maximum)


def get_bridge_message_stats(hours: int = 24) -> dict:
    """Per-bridge (bridge1/bridge2/bridge3) + Meta WhatsApp message counts:
    total, count in the given time window, unprocessed count, and
    unprocessed-and-stuck-over-1h count. Aggregate ONLY — there is no
    phone/recipient parameter, so this cannot answer "how much traffic
    went to/from this specific number." For that, use
    audit_lookup_whatsapp_messages(phone=...) instead. Distinct from
    get_social_status (that's the external Meta/Facebook social_auto_reply
    channel only)."""
    hours = _clamp(hours, 24, 24 * 30)
    return core.get("/api/whatsapp/message-stats", {"hours": hours})


def get_dlq_status(limit: int = 20) -> dict:
    """Dead-letter queue depth and recent entries — real failed/exhausted
    outbound message attempts, reused as-is from the existing arbiter."""
    limit = _clamp(limit, 20, 100)
    return core.get("/api/queue/dead-letters", {"limit": limit})


def get_bridge_diagnostics() -> dict:
    """Bridge-specific health, lag, deduplication, and failover diagnostics
    (bridge_orchestrator's own tracked state) — reused as-is."""
    return core.get("/api/bridges/diagnostics")


def get_system_health() -> dict:
    """DB, bridge heartbeats, outbound queue, disk, memory, and CPU
    (CPU probe added 2026-08-04, stdlib os.getloadavg() only, no new
    dependency). Uses the plain /health endpoint, not /health/deep — deep
    mode additionally runs a real LLM probe call on every request, an
    unnecessary cost for a monitoring tool Hermes may call frequently."""
    return core.get("/health")
