from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="FORGE_")

    project_name: str = "FORGE"
    event_name: str = "FORGE Zero Downtime Hackathon"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    database_path: str = "data/forge.db"
    audit_interval_seconds: int = 60

    brightdata_api_key: str = ""
    brightdata_browser_ws_url: str = ""
    brightdata_selenium_url: str = ""
    brightdata_api_base_url: str = "https://api.brightdata.com"

    port_client_id: str = ""
    port_client_secret: str = ""
    port_base_url: str = "https://api.getport.io/v1"

    signoz_ingestion_key: str = ""
    signoz_ingest_base_url: str = ""

    openai_api_key: str = ""
    openai_api_base_url: str = "https://api.openai.com/v1"


settings = Settings()
