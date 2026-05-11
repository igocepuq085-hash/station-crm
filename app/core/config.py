from pathlib import Path
import os

from dotenv import load_dotenv


load_dotenv()


class Settings:
    app_name: str = "station-crm"
    base_dir: Path = Path(__file__).resolve().parents[2]
    storage_dir: Path = base_dir / "storage"
    upload_dir: Path = storage_dir / "uploads"
    database_url: str = os.getenv("DATABASE_URL") or f"sqlite:///{storage_dir / 'station_crm.db'}"
    templates_dir: str = str(base_dir / "app" / "templates")
    static_dir: str = str(base_dir / "app" / "static")
    app_secret_key: str = os.getenv("APP_SECRET_KEY", "dev-change-me")
    admin_username: str = os.getenv("ADMIN_USERNAME", "admin")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "admin")
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    use_ai_summary: bool = os.getenv("USE_AI_SUMMARY", "false").strip().lower() == "true"

    @property
    def sqlalchemy_database_url(self) -> str:
        if self.database_url.startswith("postgres://"):
            return self.database_url.replace("postgres://", "postgresql+psycopg://", 1)
        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        return self.database_url

    @property
    def is_sqlite(self) -> bool:
        return self.sqlalchemy_database_url.startswith("sqlite")

    def ensure_directories(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
