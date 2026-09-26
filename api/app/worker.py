"""The Bevro worker: runs long "background" provider runs outside the API.

    python -m app.worker

One loop: take the oldest pending background run (FOR UPDATE SKIP LOCKED, so
several workers never collide), mark it running, call the adapter with a
context that persists progress and notices cancellation, then record the
result. Heartbeats go to the database every few seconds; a run whose worker
stops beating is failed honestly by the next worker to look.

Runs on the host, because that is where the logged-in Claude Code CLI and
the approved workspaces are. It shares nothing with the API but PostgreSQL
and the artifact directory.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
import uuid

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from adapters import HealthResult, get_adapter
from adapters.localroots import configured_roots
from adapters.registry import adapter_kinds, may_run_in_background
from app.config import get_settings
from app.db import get_sessionmaker
from app.domain.run_state import RunState
from app.models import Provider
from app.services import agents as agent_service
from app.services import bridges as bridge_service
from app.services import connect as connect_service
from app.services import runtime as runtime_service
from app.services import tasks as task_service
from app.services import reconcile as reconcile_service
from app.services import trust as trust_service
from app.services.providers import list_providers, record_availability, record_heartbeat
from app.services.secrets import SecretStore

log = logging.getLogger("bevro.worker")

POLL_SECONDS = 1.0
REAP_EVERY_SECONDS = 30.0
DB_RETRY_SECONDS = 5.0
AVAILABILITY_EVERY_SECONDS = 60.0
# Well inside the window the API treats as "a worker is running" (3 minutes).
HEARTBEAT_EVERY_SECONDS = 30.0


def background_kinds() -> list[str]:
    """Adapter kinds whose providers may run here (always or, for MCP, per transport)."""
    return [k for k in adapter_kinds() if may_run_in_background(get_adapter(k))]


def acquire(db, kinds: list[str], worker_id: str) -> uuid.UUID | None:
    """Claim one pending background run. Returns its id, already marked running.

    Any background run will do: a worker can drive every mechanism, which is
    what lets a run fall back from one way of reaching a provider to another.
    """
    row = db.execute(
        text(
            """
            SELECT pr.id FROM provider_runs pr
            JOIN providers p ON p.id = pr.provider_id
            WHERE pr.state = :pending AND pr.execution = 'background'
              AND pr.input_request IS NULL AND p.enabled
            ORDER BY pr.created_at
            LIMIT 1
            FOR UPDATE OF pr SKIP LOCKED
            """
        ),
        {"pending": RunState.PENDING.value},
    ).first()
    if row is None:
        db.rollback()
        return None
    run = task_service.load_run(db, row[0])
    task_service.begin_run(db, run, worker_id=worker_id)
    return run.id


class Worker:
    def __init__(self) -> None:
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self.stop = threading.Event()
        self.kinds = background_kinds()
        # Folders this worker may inspect and run things in, from the trust
        # grants; re-read before each piece of work.
        # A starting point only: the real answer is read from the trust
        # grants before each piece of work.
        self.roots = configured_roots()
        try:
            self.secrets: SecretStore | None = SecretStore()
        except RuntimeError:
            self.secrets = None
            log.warning("BEVRO_SECRET_KEY not set; running without provider secrets")

    def report_availability(self, db) -> None:
        """Tell the API which of this worker's providers can actually run here."""
        for provider in list_providers(db, enabled_only=False):
            if str(provider.adapter.get("kind", "")) not in self.kinds:
                continue
            if runtime_service.execution_of(provider) != "background":
                continue  # e.g. an MCP server over HTTP: the API reaches it, not this worker
            try:
                # The stored secrets are resolved for the check itself; the adapter looks at names only.
                secrets = self.secrets.resolve(db, provider.id) if self.secrets else {}
                # Checks every way in, so one that was down is restored here.
                outcomes, _deferred = runtime_service.check_all_runtimes(provider, secrets, location=runtime_service.WORKER)
                result = next((r for _rt, r in outcomes if r.ok), None) or (outcomes[0][1] if outcomes else runtime_service.health(provider, secrets))
            except Exception as exc:  # noqa: BLE001
                result = HealthResult(ok=False, state="unavailable", detail=str(exc)[:200])
            record_availability(db, provider, result)
            # What was just learned may change which way in is used.
            reconcile_service.reconcile(db, provider, commit=False)
            log.info("%s: %s%s", provider.slug, result.state or ("ok" if result.ok else "not ok"), f" ({result.detail})" if result.detail else "")
        db.commit()

    def catch_up(self, db) -> int:
        """Look again at providers found before discovery knew what it knows.

        Once per provider per version of discovery, on the machine that can
        see them - not on every page load, and never as something the person
        has to think about. A provider that cannot be looked at again, or
        whose folder is no longer allowed, is left exactly as it is.
        """
        from app.services import connect as connect_service
        from app.services.providers import list_providers

        done = 0
        for provider in list_providers(db, enabled_only=False):
            if not reconcile_service.needs_rediscovery(provider):
                reconcile_service.reconcile(db, provider, commit=False)
                continue
            try:
                trust_service.apply_to_process(db)
                connect_service.rediscover(db, provider)
                done += 1
            except Exception as exc:  # noqa: BLE001 - one provider must not stop the rest
                log.info("%s: couldn't look again (%s: %s)", provider.slug, type(exc).__name__, str(exc)[:120])
            # Whether or not it could be looked at again, what is written down
            # is brought back in line with whatever evidence there is.
            reconcile_service.reconcile(db, provider, commit=False)
        db.commit()
        if done:
            log.info("looked again at %s provider(s) found by an older version", done)
        return done

    def run_forever(self) -> None:
        log.info("worker %s handling adapter kinds %s; local roots %s", self.worker_id, self.kinds, [str(r) for r in self.roots] or "none")
        last_heartbeat = 0.0
        Session = get_sessionmaker()
        with Session() as db:
            # Anything already connected keeps working, and becomes something
            # the person can see and take back.
            trust_service.adopt_existing(db)
            # ...and anything found by an older Bevro is looked at again, once,
            # rather than being left saying what that older Bevro concluded.
            self.catch_up(db)
            # What the person has allowed, before the first report: checking a
            # program in an allowed folder without it says "outside the
            # allowed folders" about something that isn't.
            self.roots = trust_service.apply_to_process(db)
        last_reap = 0.0
        last_report = -AVAILABILITY_EVERY_SECONDS
        while not self.stop.is_set():
            try:
                now = time.monotonic()
                if now - last_heartbeat > HEARTBEAT_EVERY_SECONDS:
                    # Said plainly rather than inferred: on a new installation
                    # there is no work yet to infer it from, and that is
                    # exactly when Connect needs to know a worker is here.
                    with Session() as db:
                        record_heartbeat(db, self.worker_id, self.kinds)
                    last_heartbeat = now
                if now - last_report > AVAILABILITY_EVERY_SECONDS:
                    with Session() as db:
                        self.report_availability(db)
                    last_report = now
                if now - last_reap > REAP_EVERY_SECONDS:
                    with Session() as db:
                        task_service.reap_stale_runs(db)
                    last_reap = now
                with Session() as db:
                    # What the person has agreed to, read again each time
                    # round rather than remembered: a grant revoked a minute
                    # ago stops being true here, with nothing restarted.
                    self.roots = trust_service.apply_to_process(db)
                    if connect_service.run_pending(db, self.roots) or connect_service.run_reconnects(db) or bridge_service.run_pending(db, self.roots) or agent_service.run_pending(db, self.roots):
                        continue  # Connect work was handled; look again straight away
                    run_id = acquire(db, self.kinds, self.worker_id)
            except OperationalError as exc:
                # Database away (stack restarting?): wait and try again rather than die.
                log.warning("database unavailable (%s); retrying in %ss", str(exc).splitlines()[0][:120], DB_RETRY_SECONDS)
                self.stop.wait(DB_RETRY_SECONDS)
                continue
            if run_id is None:
                self.stop.wait(POLL_SECONDS)
                continue
            self.process(run_id)

    def process(self, run_id: uuid.UUID) -> None:
        Session = get_sessionmaker()
        log.info("run %s: starting", run_id)
        try:
            with Session() as db:
                run = task_service.load_run(db, run_id)
                provider: Provider = run.provider
                request = task_service.build_request(db, run, secret_store=self.secrets)
            with task_service.run_context(run_id, worker_id=self.worker_id, stopping=self.stop) as context:
                result, artifacts = task_service.execute(provider, request, context, execution="background")
            with Session() as db:
                run = task_service.load_run(db, run_id)
                task_service.finish_run(db, run, result, artifacts)
            used = (result.metadata or {}).get("runtime") or {}
            log.info("run %s: %s via %s", run_id, result.state, used.get("id", "?"))
        except Exception:  # noqa: BLE001
            log.exception("run %s crashed in the worker", run_id)
            with Session() as db:
                task_service.mark_failed(db, run_id, "Bevro hit a problem while handling this.", category=task_service.BEVRO_ERROR)

    def shutdown(self, *_: object) -> None:
        log.info("worker stopping; cancelling anything in flight")
        self.stop.set()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    worker = Worker()
    signal.signal(signal.SIGTERM, worker.shutdown)
    signal.signal(signal.SIGINT, worker.shutdown)
    worker.run_forever()
    # Say goodbye: the providers this worker served are unavailable again.
    try:
        with get_sessionmaker()() as db:
            from app.models import WorkerHeartbeat

            if (beat := db.get(WorkerHeartbeat, worker.worker_id)) is not None:
                db.delete(beat)  # no longer here; nothing should wait for it
                db.commit()
            for provider in list_providers(db, enabled_only=False):
                if str(provider.adapter.get("kind", "")) in worker.kinds and runtime_service.execution_of(provider) == "background":
                    record_availability(db, provider, HealthResult(ok=False, state="unavailable", detail="worker stopped"))
    except Exception:  # noqa: BLE001
        log.exception("could not record shutdown")


if __name__ == "__main__":
    main()
