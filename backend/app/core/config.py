import os

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="FORGE_", extra="ignore")

    project_name: str = "FORGE"
    event_name: str = "FORGE Zero Downtime Hackathon"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    database_path: str = "data/forge.db"
    audit_interval_seconds: int = 60
    auth_username: str = "admin"
    auth_password: str = "forge-local"
    auth_password_hash: str = ""
    auth_password_salt: str = "forge-local-salt"
    auth_secret: str = "change-this-for-production"
    auth_token_ttl_seconds: int = 28800

    brightdata_api_key: str = Field(default="", validation_alias=AliasChoices("BRIGHTDATA_API_KEY", "FORGE_BRIGHTDATA_API_KEY"))
    brightdata_browser_ws_url: str = Field(default="", validation_alias=AliasChoices("BRIGHTDATA_BROWSER_WS_URL", "FORGE_BRIGHTDATA_BROWSER_WS_URL"))
    brightdata_selenium_url: str = Field(default="", validation_alias=AliasChoices("BRIGHTDATA_SELENIUM_URL", "FORGE_BRIGHTDATA_SELENIUM_URL"))
    brightdata_api_base_url: str = Field(default="https://api.brightdata.com", validation_alias=AliasChoices("BRIGHTDATA_API_BASE_URL", "FORGE_BRIGHTDATA_API_BASE_URL"))

    port_client_id: str = Field(default="", validation_alias=AliasChoices("PORT_CLIENT_ID", "FORGE_PORT_CLIENT_ID"))
    port_client_secret: str = Field(default="", validation_alias=AliasChoices("PORT_CLIENT_SECRET", "FORGE_PORT_CLIENT_SECRET"))
    port_app_url: str = Field(default="https://app.getport.io/", validation_alias=AliasChoices("PORT_APP_URL", "FORGE_PORT_APP_URL"))
    port_api_base: str = Field(default="https://api.getport.io", validation_alias=AliasChoices("PORT_API_BASE", "FORGE_PORT_API_BASE"))
    port_base_url: str = Field(default="https://api.getport.io/v1", validation_alias=AliasChoices("PORT_BASE_URL", "FORGE_PORT_BASE_URL"))

    signoz_ingestion_key: str = Field(default="", validation_alias=AliasChoices("SIGNOZ_INGESTION_KEY", "FORGE_SIGNOZ_INGESTION_KEY"))
    signoz_ingest_base_url: str = Field(default="", validation_alias=AliasChoices("SIGNOZ_INGEST_BASE_URL", "FORGE_SIGNOZ_INGEST_BASE_URL"))

    anthropic_api_key: str = Field(default="", validation_alias=AliasChoices("ANTHROPIC_API_KEY", "FORGE_ANTHROPIC_API_KEY"))
    llm_provider: str = Field(default="anthropic", validation_alias=AliasChoices("FORGE_LLM_PROVIDER", "LLM_PROVIDER"))
    openai_api_key: str = Field(default="", validation_alias=AliasChoices("OPENAI_API_KEY", "FORGE_OPENAI_API_KEY"))
    openai_api_base_url: str = Field(default="https://api.openai.com/v1", validation_alias=AliasChoices("OPENAI_API_BASE_URL", "FORGE_OPENAI_API_BASE_URL"))

    @property
    def effective_port_base_url(self) -> str:
        value = (self.port_api_base or self.port_base_url or "https://api.getport.io").rstrip("/")
        if value.endswith("/v1"):
            return value
        return f"{value}/v1"


settings = Settings()
settings.port_base_url = settings.effective_port_base_url
if os.getenv("PORT_API_BASE") and not os.getenv("FORGE_PORT_API_BASE"):
    settings.port_api_base = os.getenv("PORT_API_BASE")
