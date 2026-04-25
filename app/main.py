from __future__ import annotations
import json
import time
from typing import Any, Dict, Optional

import stripe
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import text
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from .config import settings
from .db import engine, get_db, init_db
from .handlers import process_event_in_tx
from .logging_config import get_logger_with_context, setup_logging
from .middleware import MetricsMiddleware, SecurityMiddleware
from .models import EventLog

# --- Init ---
setup_logging(settings.log_level, settings.log_format, settings.log_file)
logger = get_logger_with_context("stripe_webhook")

stripe.api_key = settings.stripe_secret_key
if settings.stripe_secret_key:
    mode = "test" if settings.stripe_secret_key.startswith("sk_test_") else "live"
    logger.info(f"Stripe key loaded (mode={mode}): {settings.stripe_secret_key[:8]}****")

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Stripe Payment Service", version="1.0.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

metrics_middleware = MetricsMiddleware(app)
app.add_middleware(
    SecurityMiddleware,
    allowed_ips=settings.get_allowed_ips_list(),
    max_request_size=settings.max_request_size,
)


@app.on_event("startup")
def on_startup():
    logger.info("Starting Stripe Payment Service...")
    try:
        init_db()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("Database ready")
    except Exception as e:
        logger.error(f"Startup failed: {e}")
        raise


@app.get("/", include_in_schema=False)
def root():
    return {"ok": True}


@app.get("/healthz")
def health():
    return {"status": "ok", "timestamp": time.time()}


@app.get("/health/detailed")
def detailed_health():
    result: Dict[str, Any] = {
        "status": "ok",
        "timestamp": time.time(),
        "version": "1.0.0",
        "components": {},
    }
    try:
        with engine.connect() as conn:
            processed = conn.execute(
                text("SELECT COUNT(*) FROM event_logs WHERE status = 'processed'")
            ).scalar()
        result["components"]["database"] = {"status": "ok", "processed_events": processed}
    except Exception as e:
        result["status"] = "degraded"
        result["components"]["database"] = {"status": "error", "error": str(e)}

    try:
        stripe.Account.retrieve()
        result["components"]["stripe"] = {"status": "ok"}
    except Exception as e:
        result["status"] = "degraded"
        result["components"]["stripe"] = {"status": "error", "error": str(e)}

    return result


@app.get("/metrics")
def metrics():
    if not settings.enable_metrics:
        raise HTTPException(status_code=404, detail="Metrics disabled")
    m = metrics_middleware.get_metrics()
    lines = [
        "# HELP webhook_requests_total Total webhook requests",
        "# TYPE webhook_requests_total counter",
        f"webhook_requests_total {m['requests_total']}",
        "",
        "# HELP webhook_errors_total Total webhook errors",
        "# TYPE webhook_errors_total counter",
        f"webhook_errors_total {m['errors_total']}",
        "",
        "# HELP webhook_request_duration_seconds Request duration",
        "# TYPE webhook_request_duration_seconds histogram",
        f"webhook_request_duration_seconds_sum {m['requests_duration_seconds_sum']}",
        f"webhook_request_duration_seconds_count {m['requests_total']}",
    ]
    return Response(content="\n".join(lines), media_type="text/plain")


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------

def _verify_and_parse(payload: bytes, sig_header: Optional[str]) -> Dict[str, Any]:
    if not sig_header:
        raise HTTPException(status_code=400, detail="Missing Stripe-Signature header")
    try:
        return stripe.Webhook.construct_event(
            payload=payload, sig_header=sig_header, secret=settings.stripe_webhook_secret
        )
    except Exception as e:
        logger.error(f"Signature verification failed: {e}")
        raise HTTPException(status_code=400, detail=f"Signature verification failed: {e}")


def _payload_subset(event: Dict[str, Any]) -> Dict[str, Any]:
    if settings.store_full_payload:
        return event
    obj = (event.get("data") or {}).get("object") or {}
    return {
        "id": event.get("id"),
        "type": event.get("type"),
        "created": event.get("created"),
        "data": {
            "object": {
                "id": obj.get("id"),
                "object": obj.get("object"),
                "status": obj.get("status"),
                "customer": obj.get("customer"),
                "subscription": obj.get("subscription"),
                "payment_intent": obj.get("payment_intent"),
                "payment_status": obj.get("payment_status"),
                "amount": obj.get("amount"),
                "currency": obj.get("currency"),
                "metadata": obj.get("metadata"),
            }
        },
    }


@app.post("/webhook")
async def stripe_webhook(
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    client_ip = get_remote_address(request)
    start = time.time()
    try:
        raw = await request.body()
        sig = request.headers.get("stripe-signature")
        logger.info(f"Received webhook from {client_ip}, size={len(raw)}B")

        event = _verify_and_parse(raw, sig)
        eid = event.get("id")
        etype = event.get("type")
        created = event.get("created")

        log = db.query(EventLog).filter_by(id=eid).first()
        if not log:
            log = EventLog(
                id=eid, type=etype, created=created,
                status="received", payload=_payload_subset(event),
            )
            db.add(log)
            db.commit()

        background.add_task(_process_bg, event, eid)

        elapsed = (time.time() - start) * 1000
        logger.info(f"Webhook {eid} accepted in {elapsed:.1f}ms, queued for processing")
        return Response(status_code=200)

    except HTTPException as e:
        logger.error(f"Webhook rejected: {e.detail}")
        return Response(status_code=e.status_code, content=e.detail)
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return Response(status_code=500, content="Internal server error")


def _process_bg(event: Dict[str, Any], eid: str, retry: int = 0) -> None:
    from .db import SessionLocal

    MAX_RETRIES = settings.max_retries
    DELAYS = settings.get_retry_delays_list()

    db = SessionLocal()
    try:
        log = db.query(EventLog).filter_by(id=eid).first()
        if not log:
            log = EventLog(id=eid, type=event.get("type"), created=event.get("created"), status="received")
            db.add(log)
            db.commit()

        if not log.payload:
            log.payload = _payload_subset(event)
            db.add(log)
            db.commit()

        if log.status == "processed":
            logger.info(f"Event {eid} already processed, skipping")
            return

        process_event_in_tx(db, event)

        log.status = "processed"
        log.processed_at = datetime.now(tz=timezone.utc)
        db.add(log)
        db.commit()
        logger.info(f"Event {eid} processed successfully")

    except Exception as e:
        logger.error(f"Failed to process {eid} (attempt {retry + 1}): {e}")
        if retry < MAX_RETRIES:
            delay = DELAYS[min(retry, len(DELAYS) - 1)]
            logger.info(f"Retrying {eid} in {delay}s...")
            time.sleep(delay)
            db.close()
            return _process_bg(event, eid, retry + 1)

        try:
            log = db.query(EventLog).filter_by(id=eid).first()
            if not log:
                log = EventLog(id=eid, type=event.get("type"), created=event.get("created"))
            log.status = "error"
            log.error = f"Failed after {MAX_RETRIES} retries: {e}"
            db.add(log)
            db.commit()
        except Exception as db_err:
            logger.critical(f"Could not record error for {eid}: original={e}, db={db_err}")
    finally:
        try:
            db.close()
        except Exception:
            pass
