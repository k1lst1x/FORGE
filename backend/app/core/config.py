from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="FORGE_")

    project_name: str = "FORGE"
    event_name: str = "FORGE Zero Downtime Hackathon"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    database_path: str = "data/forge.db"
    audit_interval_seconds: int = 60


settings = Settings()
