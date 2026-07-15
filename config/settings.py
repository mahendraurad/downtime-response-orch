"""
Environment variable loader for the Learning and Memory Agent.
All secrets and connection strings come from .env file.
Never hardcode credentials in any other file.
"""
import logging
import os
from dotenv import load_dotenv
from pydantic import BaseSettings

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(module)s | %(message)s'
)
logger = logging.getLogger(__name__)
load_dotenv()


class Settings(BaseSettings):
    POSTGRES_URL: str = "postgresql+asyncpg://user:password@localhost:5432/dro_dev"
    AZURE_OPENAI_KEY: str = ""
    AZURE_OPENAI_ENDPOINT: str = ""
    AZURE_OPENAI_DEPLOYMENT: str = "gpt-4"
    AZURE_SEARCH_ENDPOINT: str = ""
    AZURE_SEARCH_KEY: str = ""
    AZURE_SEARCH_INDEX_NAME: str = "dro-learned-cases"
    AZURE_BLOB_URL: str = ""
    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"
    APP_PORT: int = 8008
    APP_ENV: str = "dev"
    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"


settings = Settings()
logger.info("Loaded environment settings for %s", settings.APP_ENV)
