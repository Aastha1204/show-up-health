from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    database_url: str = "postgresql://postgres:postgres@127.0.0.1:54329/showup"
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_jwt_secret: str = ""
    demo_mode: bool = False
    internal_tick_secret: str = ""
    public_booking_allowed_origins: str = ""
    app_timezone: str = "Asia/Kolkata"
    wa_app_secret: str = ""
    wa_verify_token: str = ""
    scheduler_enabled: bool = True
    wa_access_token: str = ""
    wa_phone_number_id: str = ""
    wa_business_account_id: str = ""
    wa_api_version: str = "v21.0"
    # Public address of this API (e.g. https://showup-api.up.railway.app): shown in Settings as the webhook URL for Meta.
    public_api_url: str = ""
    # Number of reverse proxies in front of the API whose X-Forwarded-For entry we trust (0 = use the socket address).
    trusted_proxy_hops: int = 0
    # Razorpay (optional booking deposits). Use rzp_test_ keys until you are ready to take real money.
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.public_booking_allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
