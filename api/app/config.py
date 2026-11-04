from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)
    database_url: str = "sqlite:///./data/studio.db"
    data_dir: Path = Path("./data")
    site_url: str = "http://localhost:8788"
    studio_password: str = ""
    encryption_key: str = ""
    session_secret: str = ""
    environment: str = "development"
    max_upload_bytes: int = 100 * 1024 * 1024
    allowed_hosts: list[str] = []
    studio_api_token: str = ""


settings = Settings()
