from __future__ import annotations
import logging
import stripe
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from .config import settings
from .hooks import notify_app
from .models import (
    StripeCheckoutSession,
    StripeCustomer,
    StripeInvoice,
    StripePayment,
    StripeSubscription,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Plan resolution
# ---------------------------------------------------------------------------

def _resolve_plan(price_id: Optional[str]) -> Optional[str]:
    """Resolve a Stripe price ID to a plan name using PRICE_MAP_JSON config."""
    if not price_id:
        return None
    price_map = settings.get_price_map()
    plan = price_map.get(price_id)
    if plan:
        logger.info(f"Resolved price_id {price_id} -> {plan}")
    else:
        logger.warning(f"No plan mapping for price_id: {price_id}. Add it to PRICE_MAP_JSON.")
    return plan


# ---------------------------------------------------------------------------
# Upsert helpers — each writes to Stripe mirror tables only
# ---------------------------------------------------------------------------

def _upsert_customer(db: Session, data: Dict[str, Any]) -> None:
    cid = data.get("id")
    if not cid:
        return
    obj = db.query(StripeCustomer).filter_by(stripe_customer_id=cid).first()
    if not obj:
        obj = StripeCustomer(stripe_customer_id=cid)
        db.add(obj)
        _flush_or_refetch(db, obj, StripeCustomer, stripe_customer_id=cid)
    email = data.get("email")
    if email:
        obj.email = email


def _upsert_checkout_session(db: Session, data: Dict[str, Any]) -> None:
    sid = data.get("id")
    if not sid:
        return
    obj = db.query(StripeCheckoutSession).filter_by(stripe_session_id=sid).first()
    if not obj:
        obj = StripeCheckoutSession(stripe_session_id=sid, mode=data.get("mode", "payment"))
        db.add(obj)
        _flush_or_refetch(db, obj, StripeCheckoutSession, stripe_session_id=sid)
    obj.mode = data.get("mode", obj.mode or "payment")
    obj.payment_status = data.get("payment_status")
    obj.url = data.get("url")
    md = data.get("metadata") or {}
    if isinstance(md, dict):
        obj.stripe_metadata = str(md)


def _upsert_payment_intent(db: Session, data: Dict[str, Any]) -> None:
    pid = data.get("id")
    if not pid:
        return
    obj = db.query(StripePayment).filter_by(stripe_payment_intent_id=pid).first()
    if not obj:
        obj = StripePayment(
            stripe_payment_intent_id=pid,
            amount=data.get("amount", 0),
            status=data.get("status", "requires_payment_method"),
        )
        db.add(obj)
        _flush_or_refetch(db, obj, StripePayment, stripe_payment_intent_id=pid)
    obj.amount = data.get("amount", obj.amount or 0)
    obj.currency = data.get("currency")
    obj.status = data.get("status", obj.status or "requires_payment_method")
    obj.latest_charge_id = data.get("latest_charge")
    obj.stripe_customer_id = data.get("customer")
    md = data.get("metadata") or {}
    if isinstance(md, dict):
        obj.stripe_metadata = str(md)


def _upsert_subscription(db: Session, data: Dict[str, Any]) -> Optional[str]:
    """Upsert subscription and return resolved plan name (or None)."""
    sid = data.get("id")
    if not sid:
        return None
    obj = db.query(StripeSubscription).filter_by(stripe_subscription_id=sid).first()
    if not obj:
        obj = StripeSubscription(stripe_subscription_id=sid)
        db.add(obj)
        _flush_or_refetch(db, obj, StripeSubscription, stripe_subscription_id=sid)
    obj.stripe_customer_id = data.get("customer")
    obj.status = data.get("status")

    price_id = _extract_price_id_from_subscription(data, sid)
    plan = _resolve_plan(price_id)
    if price_id:
        obj.price_id = price_id
    if plan:
        obj.plan_id = plan

    UTC = timezone.utc
    if data.get("current_period_start"):
        obj.current_period_start = datetime.fromtimestamp(data["current_period_start"], tz=UTC)
    if data.get("current_period_end"):
        obj.current_period_end = datetime.fromtimestamp(data["current_period_end"], tz=UTC)
    if data.get("cancel_at"):
        obj.cancel_at = datetime.fromtimestamp(data["cancel_at"], tz=UTC)
    if data.get("canceled_at"):
        obj.canceled_at = datetime.fromtimestamp(data["canceled_at"], tz=UTC)

    return plan


def _upsert_invoice(db: Session, data: Dict[str, Any]) -> Optional[str]:
    """Upsert invoice and return resolved plan name (or None)."""
    iid = data.get("id")
    if not iid:
        return None
    obj = db.query(StripeInvoice).filter_by(stripe_invoice_id=iid).first()
    if not obj:
        obj = StripeInvoice(
            stripe_invoice_id=iid,
            stripe_customer_id=data.get("customer"),
            stripe_subscription_id=data.get("subscription"),
            amount_due=data.get("amount_due", 0),
            amount_paid=data.get("amount_paid", 0),
            status=data.get("status", "open"),
        )
        db.add(obj)
        _flush_or_refetch(db, obj, StripeInvoice, stripe_invoice_id=iid)

    obj.stripe_subscription_id = data.get("subscription")
    obj.stripe_customer_id = data.get("customer")
    obj.status = data.get("status", obj.status or "open")
    obj.amount_due = data.get("amount_due", obj.amount_due or 0)
    obj.amount_paid = data.get("amount_paid", obj.amount_paid or 0)
    obj.currency = data.get("currency")
    obj.hosted_invoice_url = data.get("hosted_invoice_url")

    line, price_id = _extract_line_and_price_from_invoice(data, iid)
    if line:
        period = line.get("period") or {}
        if isinstance(period.get("start"), (int, float)):
            obj.period_start = datetime.fromtimestamp(period["start"], tz=timezone.utc)
        if isinstance(period.get("end"), (int, float)):
            obj.period_end = datetime.fromtimestamp(period["end"], tz=timezone.utc)
    if price_id:
        obj.price_id = price_id

    plan = _resolve_plan(price_id)

    # If plan not from invoice, try to get from linked subscription record
    if not plan and data.get("subscription"):
        sub = db.query(StripeSubscription).filter_by(
            stripe_subscription_id=data["subscription"]
        ).first()
        if sub and sub.plan_id:
            plan = sub.plan_id

    logger.info(f"invoice {iid}: period=({obj.period_start}, {obj.period_end}), price_id={obj.price_id}, plan={plan}")
    return plan


# ---------------------------------------------------------------------------
# Main event dispatcher
# ---------------------------------------------------------------------------

def process_event_in_tx(db: Session, event: Dict[str, Any]) -> None:
    """Process one Stripe event atomically. Raises on failure so the caller can retry."""
    etype = event.get("type", "")
    data = (event.get("data") or {}).get("object") or {}
    eid = event.get("id", "")

    logger.info(f"Processing event type: {etype}")

    try:
        customer_id = data.get("customer")
        plan: Optional[str] = None
        subscription_id: Optional[str] = None
        invoice_id: Optional[str] = None

        if etype == "checkout.session.completed":
            _upsert_checkout_session(db, data)
            customer_id = data.get("customer")
            if data.get("payment_status") == "paid":
                md = data.get("metadata") or {}
                plan = (
                    _norm(md.get("plan_type"))
                    or _norm(md.get("plan_code"))
                    or _norm(md.get("plan_name"))
                )

        elif etype == "payment_intent.succeeded":
            _upsert_payment_intent(db, data)
            md = data.get("metadata") or {}
            customer_id = md.get("stripe_customer_id") or data.get("customer")
            plan = _norm(md.get("plan_type")) or _norm(md.get("plan_name"))

        elif etype == "payment_intent.payment_failed":
            _upsert_payment_intent(db, data)

        elif etype in ("invoice.paid", "invoice.payment_succeeded"):
            invoice_id = data.get("id")
            subscription_id = data.get("subscription")
            plan = _upsert_invoice(db, data)

        elif etype == "invoice.payment_failed":
            invoice_id = data.get("id")
            _upsert_invoice(db, data)

        elif etype == "customer.subscription.created":
            subscription_id = data.get("id")
            plan = _upsert_subscription(db, data)

        elif etype.startswith("customer.subscription."):
            subscription_id = data.get("id")
            plan = _upsert_subscription(db, data)
            if etype == "customer.subscription.deleted":
                plan = "free"

        elif etype.startswith("customer."):
            _upsert_customer(db, data)

        else:
            logger.info(f"Unhandled event type: {etype}")

        db.commit()
        logger.info(f"DB committed for event: {etype}")

        # Notify host application (outside transaction — failures are non-fatal)
        if customer_id or plan:
            notify_app(
                event_type=etype,
                stripe_event_id=eid,
                stripe_customer_id=customer_id,
                plan=plan,
                subscription_id=subscription_id,
                invoice_id=invoice_id,
            )

    except Exception as e:
        db.rollback()
        logger.exception(f"Error processing event type={etype}: {e}")
        raise


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _norm(name: Optional[str]) -> Optional[str]:
    return name.strip().lower() if isinstance(name, str) and name.strip() else None


def _flush_or_refetch(db, obj, model, **filter_kwargs):
    try:
        db.flush()
    except Exception as e:
        db.rollback()
        logger.warning(f"Flush failed for {model.__name__}, refetching: {e}")
        obj = db.query(model).filter_by(**filter_kwargs).first()
        if not obj:
            raise
    return obj


def _extract_price_id_from_subscription(data: Dict[str, Any], sub_id: str) -> Optional[str]:
    items = (data.get("items") or {}).get("data") or []
    if items:
        price = items[0].get("price")
        if isinstance(price, str):
            return price
        if isinstance(price, dict):
            return price.get("id")

    # Fallback: expand via API
    try:
        sub = stripe.Subscription.retrieve(sub_id, expand=["items.data.price"])
        items = (sub.get("items") or {}).get("data") or []
        if items:
            price = items[0].get("price", {})
            return price.get("id") if isinstance(price, dict) else price
    except Exception as e:
        logger.warning(f"Could not expand subscription {sub_id}: {e}")
    return None


def _extract_line_and_price_from_invoice(data: Dict[str, Any], inv_id: str):
    """Return (best_line, price_id) from invoice data, fetching via API if needed."""
    lines = (data.get("lines") or {}).get("data") or []
    if not lines:
        try:
            inv_full = stripe.Invoice.retrieve(inv_id, expand=["lines.data.price"])
            lines = (inv_full.get("lines") or {}).get("data") or []
        except Exception as e:
            logger.warning(f"Could not fetch invoice lines for {inv_id}: {e}")

    # Prefer recurring/subscription lines
    line = None
    for ln in lines:
        price = ln.get("price") or {}
        if price.get("type") == "recurring" or ln.get("type") == "subscription":
            line = ln
            break
    if not line and lines:
        line = lines[0]

    price_id = None
    if line:
        price_obj = line.get("price") or {}
        price_id = price_obj.get("id") if isinstance(price_obj, dict) else None
        if not price_id:
            # Newer Stripe format: pricing.price_details.price
            pricing = line.get("pricing") or {}
            if pricing.get("type") == "price_details":
                price_id = (pricing.get("price_details") or {}).get("price")

    # Fallback: try subscription expansion
    if not price_id:
        sub_id = data.get("subscription")
        if sub_id:
            try:
                sub = stripe.Subscription.retrieve(sub_id, expand=["items.data.price"])
                items = (sub.get("items") or {}).get("data") or []
                if items:
                    price = items[0].get("price", {})
                    price_id = price.get("id") if isinstance(price, dict) else None
            except Exception as e:
                logger.warning(f"Could not expand subscription for invoice {inv_id}: {e}")

    return line, price_id
