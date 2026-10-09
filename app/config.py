from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PARIBUS_")

    directory_url: str = "https://hospital-directory.onrender.com"
    max_rows: int = 20
    max_file_bytes: int = 64 * 1024
    concurrency: int = 20
    request_timeout: float = 30.0
    max_attempts: int = 3
    backoff_base: float = 0.5
