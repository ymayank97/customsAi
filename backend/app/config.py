"""App-wide settings, loaded from env."""
from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field


def _load_local_env() -> None:
    """Populate ``os.environ`` from ``backend/.env`` for local (non-Docker) runs.

    Docker passes the file via compose ``env_file``; this covers bare ``uvicorn``.
    Real environment variables always win (``setdefault``).
    """
    env_path = pathlib.Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key:
            os.environ.setdefault(key, val.strip().strip('"').strip("'"))


_load_local_env()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Settings:
    project_root: pathlib.Path = field(default_factory=lambda: pathlib.Path(__file__).resolve().parents[2])
    data_dir: pathlib.Path = field(init=False)
    models_dir: pathlib.Path = field(init=False)

    # deployment
    app_env: str = field(default_factory=lambda: _env("APP_ENV", _env("ENV", "development")))

    # LLM
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "claude"))
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    google_api_key: str = field(default_factory=lambda: _env("GOOGLE_API_KEY"))
    claude_model: str = field(
        default_factory=lambda: _env("CLAUDE_MODEL", "claude-opus-4-8")
    )
    gemini_model: str = field(default_factory=lambda: _env("GEMINI_MODEL", "gemini-2.5-flash"))

    # Pipeline params
    batch_size: int = field(default_factory=lambda: int(_env("BATCH_SIZE", "15")))
    top_k: int = field(default_factory=lambda: int(_env("TOP_K", "8")))
    llm_concurrency: int = field(default_factory=lambda: int(_env("LLM_CONCURRENCY", "4")))
    retrieve_top_n: int = field(default_factory=lambda: int(_env("RETRIEVE_TOP_N", "50")))
    # Tier 1 (declared-first) similarity gate: when the BE description matches
    # the master entry for the declared CTH at >= this cosine score, we trust
    # the declared CTH and only verify BCD locally — no LLM call.  Lower than
    # the old (0.75) value because Tier 1 no longer requires the BCD to match
    # too; the goal is to *check* BCD, not require it.
    tier1_sim_threshold: float = field(default_factory=lambda: float(_env("TIER1_SIM_THRESHOLD", "0.65")))
    # Lexical-mode Tier 1 gate (TF-IDF cosine has a different scale than the
    # dense embedding cosine above; calibrated on data/input_data.xlsx).
    tier1_lexical_sim_threshold: float = field(
        default_factory=lambda: float(_env("TIER1_LEXICAL_SIM_THRESHOLD", "0.30"))
    )

    # Retrieval score gap top1-top2 below this marks ``ambiguous_retrieval`` review.
    # Calibrated for the lexical hierarchical retriever, whose ``adjusted`` scores
    # are NOT min-max-normalised (top ~1.3 with additive head-noun/leaf bonuses up
    # to 0.30). The old 0.08 default was scaled for [0,1] cosine and barely bit;
    # 0.12 sits at the p33 of the margin distribution on the adversarial set,
    # aligning the "ambiguous" net with the natural gap between confident and
    # contestable top-pairs.
    retrieval_rerank_margin_threshold: float = field(
        default_factory=lambda: float(_env("RETRIEVAL_RERANK_MARGIN_THRESHOLD", "0.12"))
    )
    # Jaccard (+ notification hint) threshold to treat a concessional variant as applicable.
    condition_match_threshold: float = field(
        default_factory=lambda: float(_env("CONDITION_MATCH_THRESHOLD", "0.12"))
    )

    # CORS
    allowed_origins: list[str] = field(default_factory=lambda: [
        o.strip() for o in _env("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:8000").split(",") if o.strip()
    ])

    # Auth (OAuth + JWT). When both are set, protected routes require a valid Bearer token.
    google_oauth_client_id: str = field(default_factory=lambda: _env("GOOGLE_OAUTH_CLIENT_ID"))
    jwt_secret: str = field(default_factory=lambda: _env("JWT_SECRET"))
    jwt_exp_days: int = field(default_factory=lambda: int(_env("JWT_EXPIRE_DAYS", "1")))
    premium_emails: list[str] = field(default_factory=lambda: [
        email.strip().lower() for email in _env("PREMIUM_EMAILS").split(",") if email.strip()
    ])
    admin_emails: list[str] = field(default_factory=lambda: [
        email.strip().lower() for email in _env("ADMIN_EMAILS").split(",") if email.strip()
    ])

    # Max upload bytes (multipart). Excel row apps: default 50 MB ceiling.
    max_upload_bytes: int = field(default_factory=lambda: int(_env("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024))))
    # When true and DATABASE_URL is Postgres, run `alembic upgrade head` during API lifespan (before DB use).
    auto_migrate: bool = field(
        default_factory=lambda: _env("AUTO_MIGRATE", "0").lower() in ("1", "true", "yes")
    )

    # Observability (Phase B)
    sentry_dsn: str = field(default_factory=lambda: _env("SENTRY_DSN"))
    sentry_traces_sample_rate: float = field(
        default_factory=lambda: float(_env("SENTRY_TRACES_SAMPLE_RATE", "0"))
    )

    def __post_init__(self):
        self.data_dir = self.project_root / "data" / "enriched"
        self.models_dir = self.data_dir / "models"

    @property
    def auth_enforced(self) -> bool:
        return bool(self.google_oauth_client_id and self.jwt_secret)

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in ("production", "prod")


settings = Settings()
