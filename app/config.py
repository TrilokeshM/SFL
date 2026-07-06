"""
Central configuration. Everything tunable lives here and is read from
environment variables (see .env.example) so nothing is hardcoded.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


class Settings:
    # --- LLM ---
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")

    # --- App ---
    SKILL_CATALOG_PATH: str = os.getenv("SKILL_CATALOG_PATH", "data/skill_catalog.json")
    MAX_ZIP_SIZE_MB: int = _int("MAX_ZIP_SIZE_MB", 25)
    MAX_FILES_READ: int = _int("MAX_FILES_READ", 40)
    MAX_FILE_READ_BYTES: int = _int("MAX_FILE_READ_BYTES", 20_000)
    DEFAULT_QUESTIONS_PER_SKILL: int = _int("DEFAULT_QUESTIONS_PER_SKILL", 2)

    # --- Proctoring thresholds (seconds unless noted) ---
    GAZE_OFF_LOW_S: float = _float("GAZE_OFF_LOW_S", 3.0)
    GAZE_OFF_MEDIUM_S: float = _float("GAZE_OFF_MEDIUM_S", 8.0)
    FACE_NOT_DETECTED_LOW_S: float = _float("FACE_NOT_DETECTED_LOW_S", 3.0)
    FACE_NOT_DETECTED_MEDIUM_S: float = _float("FACE_NOT_DETECTED_MEDIUM_S", 8.0)

    # Weight applied per severity when computing the integrity score deduction
    SEVERITY_WEIGHT_LOW: float = _float("SEVERITY_WEIGHT_LOW", 0.02)
    SEVERITY_WEIGHT_MEDIUM: float = _float("SEVERITY_WEIGHT_MEDIUM", 0.06)
    SEVERITY_WEIGHT_HIGH: float = _float("SEVERITY_WEIGHT_HIGH", 0.15)

    # Risk level cut points on the final 0-1 integrity_score
    RISK_LOW_MIN_SCORE: float = _float("RISK_LOW_MIN_SCORE", 0.75)
    RISK_MEDIUM_MIN_SCORE: float = _float("RISK_MEDIUM_MIN_SCORE", 0.5)

    # If no event at all is received for this many seconds, the session
    # is considered to have dropped and is flagged automatically.
    CONNECTION_TIMEOUT_S: float = _float("CONNECTION_TIMEOUT_S", 12.0)
    WATCHDOG_POLL_INTERVAL_S: float = _float("WATCHDOG_POLL_INTERVAL_S", 3.0)


settings = Settings()
