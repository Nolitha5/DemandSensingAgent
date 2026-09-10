"""Application configuration loaded from environment variables."""
from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App
    app_name: str = "Demand Sensing API"
    app_version: str = "1.0.0"
    debug: bool = False
    environment: str = "development"

    # Firebase
    firebase_project_id: str = "smmes-7adc8"
    firebase_api_key: str = "AIzaSyDsPL3rAVcwiYdrXUSnXcvIuqYry_0kamw"
    firebase_auth_domain: str = "smmes-7adc8.firebaseapp.com"
    firebase_database_url: str = "https://smmes-7adc8-default-rtdb.firebaseio.com"
    firebase_storage_bucket: str = "smmes-7adc8.firebasestorage.app"
    # Path to service account JSON for server-side Firestore access
    google_application_credentials: Optional[str] = None
    use_firestore_emulator: bool = False
    firestore_emulator_host: Optional[str] = None

    # Data
    sample_data_path: str = "app/data/demand_sensing_mock_data"
    store_id: str = "STORE-001"

    # Agent defaults
    forecast_horizon_days: int = 7
    min_observations_for_seasonality: int = 28   # 4 weeks
    min_observations_for_stl: int = 60
    min_observations_for_regression: int = 42
    outlier_iqr_multiplier: float = 3.0
    outlier_zscore_threshold: float = 3.5
    forecast_drift_threshold: float = 0.20       # WAPE drift > 20% triggers alert
    wape_alert_threshold: float = 0.30           # WAPE > 30% → DEGRADED
    bias_alert_threshold: float = 0.15

    # OpenAI (optional — only for explanations)
    openai_api_key: Optional[str] = None
    use_llm_explanations: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


@lru_cache()
def get_settings() -> Settings:
    return Settings()
