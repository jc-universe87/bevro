"""The Bevro scheduler: the process that knows what time it is.

    python -m app.scheduler

One loop. Take the automations that are due (PostgreSQL decides who gets
each one), create an ordinary Task for each, run the quick ones here and
leave the long ones for the worker, then look at what has finished and
decide whether a monitoring result is worth surfacing.

It holds no state of its own: everything is a row, so several schedulers can
run, and one can be stopped and started without losing or repeating work.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time

from sqlalchemy.exc import OperationalError

from app.config import get_settings
from app.db import get_sessionmaker
from app.services import automations as automation_service
from app.services import notifications as notification_service
from app.services import tasks as task_service
from app.services.secrets import SecretStore

log = logging.getLogger("bevro.scheduler")

TICK_SECONDS = 20.0
DB_RETRY_SECONDS = 5.0


def condition_model():
    """The small model that judges a monitoring condition, when one is set up."""
    settings = get_settings()
    if settings.router_mode.lower() != "llm":
        return None
    try:
        from app.routing.models import get_routing_model

        return get_routing_model(settings)
    except Exception as exc:  # noqa: BLE001
        log.info("no model for monitoring conditions (%s); results are compared instead", exc)
        return None


class Scheduler:
    def __init__(self) -> None:
        self.id = f"{socket.gethostname()}:{os.getpid()}"
        self.stop = threading.Event()
        try:
            self.secrets: SecretStore | None = SecretStore()
        except RuntimeError:
            self.secrets = None
            log.warning("BEVRO_SECRET_KEY not set; scheduled runs will have no provider credentials")

    def tick(self, db) -> int:
        """One turn: start what is due, pick up any straggler, close what has
        finished, and send whatever is waiting to go out."""
        started = 0
        for automation, run in automation_service.claim_due(db):
            task = automation_service.start_run(db, automation, run)
            db.commit()
            if task is None:
                log.info("automation %s: nothing ran this turn (%s)", automation.id, run.reason)
                continue
            started += 1
            self._drive(db, task)
        started += self._drive_waiting(db)
        started += automation_service.settle_finished(db, model=condition_model())
        # Telling the person is separate work: a message that cannot be sent
        # is tried again here, and never by running the provider again.
        notification_service.deliver_pending(db)
        return started

    def _drive_waiting(self, db) -> int:
        """An occurrence whose task never got going - the API restarted, say -
        is picked up here rather than sitting in the queue for ever."""
        driven = 0
        for run in automation_service.waiting_runs(db):
            task = task_service.get_task(db, run.task_id)
            if task is None:
                continue
            self._drive(db, task)
            driven += 1
        return driven

    def _drive(self, db, task) -> None:
        """Quick providers run here; long ones are the worker's, as always."""
        run = task.runs[-1] if task.runs else None
        if run is None or run.execution != "inline" or run.state != "pending":
            return
        try:
            task_service.execute_run(db, run.id, secret_store=self.secrets)
        except Exception:  # noqa: BLE001 - one bad provider must not stop the clock
            log.exception("scheduled run %s failed in the scheduler", run.id)
            db.rollback()
            task_service.mark_failed(db, run.id, "Bevro hit a problem while handling this.", category=task_service.BEVRO_ERROR)

    def run_forever(self) -> None:
        log.info("scheduler %s started", self.id)
        Session = get_sessionmaker()
        while not self.stop.is_set():
            try:
                with Session() as db:
                    self.tick(db)
                with Session() as db:
                    # The scheduler always runs, with or without the worker:
                    # it is where work nobody is driving any more is noticed.
                    task_service.reap_stale_runs(db)
            except OperationalError as exc:
                log.warning("database unavailable (%s); retrying in %ss", str(exc).splitlines()[0][:120], DB_RETRY_SECONDS)
                self.stop.wait(DB_RETRY_SECONDS)
                continue
            except Exception:  # noqa: BLE001
                log.exception("scheduler turn failed; carrying on")
            self.stop.wait(TICK_SECONDS)
        log.info("scheduler %s stopped", self.id)

    def shutdown(self, *_: object) -> None:
        self.stop.set()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    scheduler = Scheduler()
    signal.signal(signal.SIGTERM, scheduler.shutdown)
    signal.signal(signal.SIGINT, scheduler.shutdown)
    scheduler.run_forever()


if __name__ == "__main__":
    main()
