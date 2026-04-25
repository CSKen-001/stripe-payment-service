"""
Callback notification to the host application.

When a Stripe event is processed, this module sends a POST request to CALLBACK_URL
so the host app can update its own database (e.g., grant plan access to a user).

Callback payload example:
{
    "event_type": "customer.subscription.created",
    "stripe_event_id": "evt_xxx",
    "stripe_customer_id": "cus_xxx",
    "plan": "pro",
    "subscription_id": "sub_xxx",
    "invoice_id": null
}

The host app should respond with 2xx. Non-2xx responses are logged as warnings.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import logging
from typing import Any, Dict, Optional

import httpx

from .config import settings

logger = logging.getLogger(__name__)


def _make_signature(body: str) -> Optional[str]:
    """Generate HMAC-SHA256 signature for callback authentication."""
    if not settings.callback_secret:
        return None
    sig = hmac.new(settings.callback_secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def notify_app(
    event_type: str,
    stripe_event_id: str,
    stripe_customer_id: Optional[str],
    plan: Optional[str] = None,
    subscription_id: Optional[str] = None,
    invoice_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Send a notification to the host application's callback URL.
    Failures are logged but never raise — payment processing must not fail due to callback errors.
    """
    if not settings.callback_url:
        return

    payload: Dict[str, Any] = {
        "event_type": event_type,
        "stripe_event_id": stripe_event_id,
        "stripe_customer_id": stripe_customer_id,
        "plan": plan,
        "subscription_id": subscription_id,
        "invoice_id": invoice_id,
    }
    if extra:
        payload.update(extra)

    body = json.dumps(payload)
    headers = {"Content-Type": "application/json"}
    sig = _make_signature(body)
    if sig:
        headers["X-Webhook-Signature"] = sig

    try:
        resp = httpx.post(
            settings.callback_url,
            content=body,
            headers=headers,
            timeout=settings.callback_timeout,
        )
        if resp.status_code >= 400:
            logger.warning(
                f"Callback returned {resp.status_code} for event {stripe_event_id}: {resp.text[:200]}"
            )
        else:
            logger.info(f"Callback succeeded for event {stripe_event_id} ({resp.status_code})")
    except Exception as e:
        logger.warning(f"Callback failed for event {stripe_event_id}: {e}")
