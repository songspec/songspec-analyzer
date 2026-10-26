"""Settings, all from environment variables (prefix SONGSPEC_), so the main dev configures the service without touching code.

Every variable and its default is listed in INTEGRATION.md.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name, default):
    return os.environ.get("SONGSPEC_" + name, default)


def _bool(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _list(v):
    return [s.strip() for s in str(v).split(",") if s.strip()]


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "data")).resolve())
    max_upload_mb: float = field(default_factory=lambda: float(_env("MAX_UPLOAD_MB", "100")))
    max_duration_s: float = field(default_factory=lambda: float(_env("MAX_DURATION_S", str(15 * 60))))
    audio_ttl_s: float = field(default_factory=lambda: float(_env("AUDIO_TTL_S", str(24 * 3600))))
    workers: int = field(default_factory=lambda: int(_env("WORKERS", "1")))
    queue: str = field(default_factory=lambda: _env("QUEUE", "inline"))               # inline | redis
    redis_url: str = field(default_factory=lambda: _env("REDIS_URL", "redis://localhost:6379/0"))
    cors_origins: list = field(default_factory=lambda: _list(_env("CORS_ORIGINS", "*")))
    api_keys: list = field(default_factory=lambda: _list(_env("API_KEYS", "")))       # empty = no key needed
    rate_per_min: float = field(default_factory=lambda: float(_env("RATE_PER_MIN", "10")))   # new analyses per client per minute
    rate_burst: int = field(default_factory=lambda: int(_env("RATE_BURST", "5")))
    serve_demo: bool = field(default_factory=lambda: _bool(_env("SERVE_DEMO", "0")))   # /demo + /client static files (dev only)
    high_accuracy_default: bool = field(default_factory=lambda: _bool(_env("HIGH_ACCURACY", "0")))
    models_dir: Path = field(default_factory=lambda: Path(_env("MODELS_DIR", str(Path(__file__).resolve().parents[1] / "models"))))
    hooktheory_user: str = field(default_factory=lambda: _env("HOOKTHEORY_USER", ""))
    hooktheory_password: str = field(default_factory=lambda: _env("HOOKTHEORY_PASSWORD", ""))
    spotify_client_id: str = field(default_factory=lambda: _env("SPOTIFY_CLIENT_ID", ""))
    spotify_client_secret: str = field(default_factory=lambda: _env("SPOTIFY_CLIENT_SECRET", ""))

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "analyzer.sqlite3"

    @property
    def fixed_dir(self) -> Path:
        """Fix BPM outputs, kept for the requester until the audio TTL."""
        return self.data_dir / "fixed"

    def ensure_dirs(self):
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.fixed_dir.mkdir(parents=True, exist_ok=True)


_settings = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
        _settings.ensure_dirs()
    return _settings


def set_settings(s: Settings):
    """Tests and embedding apps can install their own settings."""
    global _settings
    s.ensure_dirs()
    _settings = s
