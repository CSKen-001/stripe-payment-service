from __future__ import annotations
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Integer, DateTime, Text, JSON, func, BigInteger

from .config import settings


class Base(DeclarativeBase):
    pass


class EventLog(Base):
    """Idempotency log for all received Stripe events."""
    __tablename__ = "event_logs"
    __table_args__ = (
        {"sqlite_autoincrement": True} if "sqlite" in settings.get_database_url().lower() else {}
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)       # Stripe event id
    type: Mapped[str] = mapped_column(String(64), index=True)
    created: Mapped[int] = mapped_column(BigInteger, index=True)         # epoch seconds
    received_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    processed_at: Mapped["DateTime | None"] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="received", index=True)  # received|processed|error
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class StripeCustomer(Base):
    __tablename__ = "stripe_customers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(64), index=True, unique=True)
    email: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    created_at: Mapped["DateTime"] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped["DateTime"] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class StripeCheckoutSession(Base):
    __tablename__ = "stripe_checkout_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stripe_session_id: Mapped[str | None] = mapped_column(String(64), index=True, unique=True)
    mode: Mapped[str | None] = mapped_column(String(16))
    payment_status: Mapped[str | None] = mapped_column(String(32))
    price_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    plan_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    client_reference_id: Mapped[str | None] = mapped_column(String(64))
    stripe_metadata: Mapped[str | None] = mapped_column("metadata", Text)
    url: Mapped[str | None] = mapped_column(String)
    completed_at: Mapped["DateTime | None"] = mapped_column(DateTime, nullable=True)
    created_at: Mapped["DateTime"] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped["DateTime"] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class StripePayment(Base):
    __tablename__ = "stripe_payments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String(64), index=True, unique=True)
    amount: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[str | None] = mapped_column(String(32))
    latest_charge_id: Mapped[str | None] = mapped_column(String(64))
    stripe_customer_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    stripe_metadata: Mapped[str | None] = mapped_column("metadata", Text)
    created_at: Mapped["DateTime"] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped["DateTime"] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class StripeSubscription(Base):
    __tablename__ = "stripe_subscriptions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(64), index=True, unique=True)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(64), index=True)
    price_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    plan_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str | None] = mapped_column(String(32))
    current_period_start: Mapped["DateTime | None"] = mapped_column(DateTime, nullable=True)
    current_period_end: Mapped["DateTime | None"] = mapped_column(DateTime, nullable=True)
    cancel_at: Mapped["DateTime | None"] = mapped_column(DateTime, nullable=True)
    canceled_at: Mapped["DateTime | None"] = mapped_column(DateTime, nullable=True)
    created_at: Mapped["DateTime"] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped["DateTime"] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class StripeInvoice(Base):
    __tablename__ = "stripe_invoices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stripe_invoice_id: Mapped[str | None] = mapped_column(String(64), index=True, unique=True)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(64), index=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(64), index=True)
    amount_due: Mapped[int | None] = mapped_column(Integer)
    amount_paid: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[str | None] = mapped_column(String(32))
    hosted_invoice_url: Mapped[str | None] = mapped_column(String)
    period_start: Mapped["DateTime | None"] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped["DateTime | None"] = mapped_column(DateTime(timezone=True), nullable=True)
    price_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped["DateTime"] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped["DateTime"] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
