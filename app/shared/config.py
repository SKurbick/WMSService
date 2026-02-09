"""Конфигурация приложения"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    """Настройки приложения из переменных окружения"""

    # Приложение
    APP_NAME: str = "WMS Service"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # База данных
    DB_HOST: str
    DB_PORT: int = 5432
    DB_USER: str
    DB_PASSWORD: str
    DB_NAME: str

    # API
    API_V1_PREFIX: str = "/api"

    # JWT (для будущего)
    JWT_SECRET_KEY: Optional[str] = None
    JWT_ALGORITHM: str = "HS256"

    # Внешние сервисы
    PRODUCTS_SERVICE_URL: Optional[str] = None

    # Настройки Pydantic v2
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )

    @property
    def database_url(self) -> str:
        """Формирует URL для подключения к БД"""
        return f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"


settings = Settings()