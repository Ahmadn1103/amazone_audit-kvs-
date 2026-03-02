"""Application configuration"""
from pathlib import Path
from pydantic_settings import BaseSettings
from functools import lru_cache

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    """App settings from env vars"""
    # API
    API_PREFIX: str = "/api"
    
    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "https://*.vercel.app"]
    
    # AWS
    AWS_REGION: str = "us-east-1"
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    S3_BUCKET: str = "amazon-audit-uploads"
    COGNITO_USER_POOL_ID: str = ""
    COGNITO_CLIENT_ID: str = ""
    DYNAMODB_TABLE: str = "amazon-audit-reports"

    # Database (RDS PostgreSQL)
    DATABASE_URL: str = ""

    # Perplexity Sonar (real-time benchmarks)
    PERPLEXITY_API_KEY: str = ""
    
    class Config:
        env_file = str(_ENV_FILE)
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
