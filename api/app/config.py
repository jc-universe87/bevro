from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.version import __version__


class Settings(BaseSettings):
    """Runtime configuration, read from the environment."""

    model_config = SettingsConfigDict(env_prefix="BEVRO_", extra="ignore")

    database_url: str = "postgresql+psycopg://bevro:bevro@db:5432/bevro"
    # Filesystem root for artifact files (V1 storage backend).
    artifact_dir: str = "/data/artifacts"
    # Fernet key used to encrypt provider secrets at rest. Generate with
    # `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
    # Leave empty in development: a key is generated once and kept in secret_key_file.
    secret_key: str = ""
    secret_key_file: str = "/data/secret.key"
    # Origins allowed to call the API from a browser (the Vite dev server).
    cors_origins: str = "http://localhost:6140"
    app_version: str = __version__
    # Server-side registry of directories coding providers may work in. Optional:
    # without it, coding providers simply have nowhere to work.
    workspaces_file: str = "/srv/config/workspaces.json"
    # Technical logs of background runs (never served to the browser).
    log_dir: str = "/data/logs"
    # A background run whose worker has not reported for this long is presumed dead.
    worker_stale_seconds: int = 90

    # Routing. "deterministic" needs nothing and calls nothing. "llm" sends the
    # request and a sanitised provider catalogue to the configured model.
    router_mode: str = "deterministic"
    router_backend: str = "openai"  # or "anthropic"; both speak plain HTTP
    router_model: str = ""  # backend default when empty
    router_api_key: str = ""
    router_base_url: str = ""  # backend default when empty; any OpenAI-compatible server works
    router_timeout_seconds: float = 8.0

    # Connect. Local folders Bevro may inspect and run things in, on the
    # machine doing the work (":"- or ","-separated, ~ allowed). Empty means
    # no local discovery; the worker reads the same variable from .env.
    local_roots: str = ""
    # Bevro-side storage for connected providers that need a place to run
    # (typed commands without a folder). Never inside the provider's own project.
    integrations_dir: str = "/data/integrations"
    # Where agents Bevro was asked to create live. Runtime data, not source:
    # each is an ordinary project that Bevro happens to keep.
    agents_dir: str = "/data/agents"
    # "auto": when routing is in llm mode, let the same model refine a draft's
    # name, description and capabilities from sanitised evidence. "off": never.
    discovery_assist: str = "auto"
    # A provider may be reachable several ways. When one turns out to be down,
    # Bevro tries the next. A run that timed out may have started real work, so
    # it is not retried elsewhere unless this is turned on.
    runtime_fallback_on_timeout: bool = False

    # Where this Bevro can be reached, for the links in emails and webhooks.
    app_url: str = "http://localhost:6140"

    # A normal workspace starts empty: the agents in it are the ones you
    # connected or created. Turn this on for a demo, a screenshot or a
    # walkthrough and Bevro also registers its example providers.
    demo_mode: bool = False

    # Delivery. None of it is required: with nothing set up, Bevro still
    # notifies inside Bevro and no external message is ever attempted.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    smtp_timeout_seconds: float = 15.0
    # Who Bevro emails. Without it, email is simply not offered.
    notify_email: str = ""
    # A web address of your own to post events to. Optional.
    notify_webhook_url: str = ""
    notify_webhook_timeout_seconds: float = 10.0
    # Shared secret for signing webhook deliveries. With one set, every post
    # carries an X-Bevro-Signature the receiver can check. Never leaves the
    # server and is never returned to the browser.
    notify_webhook_secret: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
