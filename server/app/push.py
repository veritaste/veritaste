from __future__ import annotations

import json
import logging

from .config import VAPID_PRIVATE, VAPID_PUBLIC, VAPID_SUB

log = logging.getLogger("veritaste.push")

DEAD_STATUSES = (404, 410)

DEFAULT_TTL_S = 4 * 3600

_DIAGNOSTIC_HEADERS = (
    "x-wns-status", "x-wns-error-description", "x-wns-debug-trace",
    "x-wns-msg-id", "ttl", "retry-after",
)


def enabled() -> bool:
    return bool(VAPID_PUBLIC and VAPID_PRIVATE)


def public_key() -> str:
    return VAPID_PUBLIC


def send(sub: dict, title: str, body: str, url: str = "/",
         ttl: int = DEFAULT_TTL_S) -> tuple[bool, int | None, str | None]:
    if not enabled():
        return False, None, "notifications are not configured"

    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        log.warning("pywebpush not installed; push disabled")
        return False, None, "push library missing"

    try:
        webpush(
            subscription_info=sub,
            data=json.dumps({"title": title, "body": body, "url": url}),
            vapid_private_key=VAPID_PRIVATE,
            vapid_claims={"sub": VAPID_SUB},
            ttl=ttl,
            timeout=10,
        )
        return True, 201, None
    except WebPushException as exc:
        resp = getattr(exc, "response", None)
        status = getattr(resp, "status_code", None)
        headers = {k.lower(): v for k, v in dict(getattr(resp, "headers", {}) or {}).items()}
        detail = {k: headers[k] for k in _DIAGNOSTIC_HEADERS if k in headers}
        reason = (headers.get("x-wns-error-description")
                  or (getattr(resp, "text", "") or "").strip()[:200]
                  or str(exc)[:200])
        level = log.info if status in DEAD_STATUSES else log.warning
        level("push failed (status=%s) %s: %s", status, detail or "", reason)
        return False, status, reason
    except Exception as exc:
        log.warning("push transport failed: %s", exc)
        return False, None, str(exc)[:200]
