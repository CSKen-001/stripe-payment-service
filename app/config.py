from __future__ import annotations
import json
from typing import Optional, List, Dict
from pydantic import validator, Field
from pydantic_settings import BaseSettings
import ipaddress


class Settings(BaseSettings):
    """Application settings"""

    # Stripe
    stripe_secret_key: str = Field(..., env="STRIPE_SECRET_KEY")
    stripe_webhook_secret: str = Field(..., env="STRIPE_WEBHOOK_SECRET")

    # Database
    database_url: str = Field(default="sqlite:///./webhook_state.db", env="DATABASE_URL")
    database_host: Optional[str] = Field(default=None, env="DATABASE_HOST")
    database_port: Optional[int] = Field(default=5432, env="DATABASE_PORT")
    database_name: Optional[str] = Field(default=None, env="DATABASE_NAME")
    database_user: Optional[str] = Field(default=None, env="DATABASE_USER")
    database_password: Optional[str] = Field(default=None, env="DATABASE_PASSWORD")
    db_pool_size: int = Field(default=10, env="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=20, env="DB_MAX_OVERFLOW")
    db_pool_timeout: int = Field(default=30, env="DB_POOL_TIMEOUT")

    # Callback URL — the app that uses this service must implement this endpoint.
    # When a payment event is processed, a POST request is sent here with event details.
    # Example: https://myapp.com/api/payment-callback
    callback_url: Optional[str] = Field(default=None, env="CALLBACK_URL")
    callback_secret: Optional[str] = Field(default=None, env="CALLBACK_SECRET")  # HMAC secret for callback auth
    callback_timeout: int = Field(default=10, env="CALLBACK_TIMEOUT")  # seconds

    # Price ID → Plan name mapping (JSON format)
    # Example: {"price_xxx": "standard", "price_yyy": "pro", "price_zzz": "enterprise"}
    price_map_json: str = Field(default="{}", env="PRICE_MAP_JSON")

    # Security
    store_full_payload: bool = Field(default=False, env="STORE_FULL_PAYLOAD")
    max_request_size: int = Field(default=1024 * 1024, env="MAX_REQUEST_SIZE")
    rate_limit: str = Field(default="100/minute", env="RATE_LIMIT")
    allowed_ips: Optional[str] = Field(default=None, env="ALLOWED_IPS")

    # Logging
    log_level: str = Field(default="INFO", env="LOG_LEVEL")
    log_format: str = Field(default="json", env="LOG_FORMAT")
    log_file: str = Field(default="data/log/webhook.log", env="LOG_FILE")

    # Retry
    max_retries: int = Field(default=3, env="MAX_RETRIES")
    retry_delays: str = Field(default="1,5,15", env="RETRY_DELAYS")

    # Monitoring
    enable_metrics: bool = Field(default=True, env="ENABLE_METRICS")

    @validator("stripe_secret_key")
    def validate_stripe_secret_key(cls, v):
        if not v.startswith(("sk_test_", "sk_live_")):
            raise ValueError("STRIPE_SECRET_KEY must start with sk_test_ or sk_live_")
        return v

    @validator("stripe_webhook_secret")
    def validate_stripe_webhook_secret(cls, v):
        if not v.startswith("whsec_"):
            raise ValueError("STRIPE_WEBHOOK_SECRET must start with whsec_")
        return v

    @validator("log_level")
    def validate_log_level(cls, v):
        valid = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in valid:
            raise ValueError(f"LOG_LEVEL must be one of {valid}")
        return v.upper()

    @validator("log_format")
    def validate_log_format(cls, v):
        if v not in ["json", "text"]:
            raise ValueError("LOG_FORMAT must be 'json' or 'text'")
        return v

    @validator("allowed_ips")
    def validate_allowed_ips(cls, v):
        if v is None:
            return None
        ips = [ip.strip() for ip in v.split(",") if ip.strip()]
        for ip in ips:
            try:
                ipaddress.ip_network(ip, strict=False)
            except ipaddress.AddressValueError:
                raise ValueError(f"Invalid IP address or network: {ip}")
        return ",".join(ips)

    def get_allowed_ips_list(self) -> List[str]:
        if not self.allowed_ips:
            return []
        return [ip.strip() for ip in self.allowed_ips.split(",") if ip.strip()]

    def get_retry_delays_list(self) -> List[int]:
        return [int(d.strip()) for d in self.retry_delays.split(",") if d.strip()]

    def get_database_url(self) -> str:
        if all([self.database_host, self.database_name, self.database_user, self.database_password]):
            return (
                f"postgresql+psycopg2://{self.database_user}:{self.database_password}"
                f"@{self.database_host}:{self.database_port}/{self.database_name}"
            )
        return self.database_url

    def get_price_map(self) -> Dict[str, str]:
        """Parse PRICE_MAP_JSON into a dict."""
        try:
            return json.loads(self.price_map_json)
        except (json.JSONDecodeError, TypeError):
            return {}

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


settings = Settings()
