import logging
import threading
import time
from typing import Optional

logger = logging.getLogger("hermes.plugins.d1_mem")


class QueueFlusher:
    def __init__(self, queue_store, d1_client, *, batch_size: int = 25, interval_seconds: int = 3, max_attempts: int = 6):
        self.queue_store = queue_store
        self.d1_client = d1_client
        self.batch_size = max(1, int(batch_size))
        self.interval_seconds = max(1, int(interval_seconds))
        self.max_attempts = max(1, int(max_attempts))
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="d1-mem-flusher", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.flush_once()
            except Exception as e:
                logger.error("D1 queue flusher loop failed: %s", e)
            self._stop.wait(self.interval_seconds)

    def flush_once(self) -> None:
        jobs = self.queue_store.claim_batch(limit=self.batch_size)
        for job in jobs:
            try:
                self.d1_client.upsert_memory(job["payload"])
                self.queue_store.ack(job["job_id"])
            except Exception as e:
                self.queue_store.fail(job, str(e), self.max_attempts)

    def shutdown(self, timeout: int = 10) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        try:
            self.queue_store.release_sending()
        except Exception:
            pass
        try:
            self.flush_once()
        except Exception:
            pass
