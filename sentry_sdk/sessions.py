import uuid
import time
from datetime import datetime
from threading import Thread, Lock
from weakref import ref as weakref

from sentry_sdk._types import MYPY

if MYPY:
    from typing import Optional
    from typing import Union
    from typing import Any
    from typing import Dict

    from sentry_sdk.hub import Hub
    from sentry_sdk._types import SessionStatus


def _timestamp(
    dt  # type: datetime
):
    # type: (...) -> str
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _make_uuid(
    val  # type: Union[str, uuid.UUID]
):
    # type: (...) -> uuid.UUID
    if isinstance(val, uuid.UUID):
        return val
    return uuid.UUID(val)


class SessionFlusher(object):
    def __init__(
        self,
        flush_func,  # type: Any
        flush_interval=10,  # type: int
    ):
        # type: (...) -> None
        self.flush_func = flush_func
        self.flush_interval = 10
        self.pending = {}  # type: Dict[str, Any]
        self._thread = None  # type: Optional[Thread]
        self._thread_lock = Lock()
        self._running = True

        # TODO: this needs fork handling same as `BackgroundWorker`

    def flush(self):
        # type: (...) -> None
        pending = self.pending
        self.pending = {}
        self.flush_func(list(pending.values()))

    def _ensure_running(self):
        # type: (...) -> None
        if self._thread is not None:
            return None
        with self._thread_lock:
            if self._thread is not None:
                return None

            def _thread():
                # type: (...) -> None
                while self._running:
                    time.sleep(self.flush_interval)
                    if self.pending and self._running:
                        self.flush()

            thread = Thread(target=_thread)
            thread.daemon = True
            thread.start()
            self._thread = thread
        return None

    def add_session(
        self, session  # type: Session
    ):
        # type: (...) -> None
        self.pending[session.sid.hex] = session.to_json()
        self._ensure_running()

    def kill(self):
        # type: (...) -> None
        self._running = False

    def __del__(self):
        # type: (...) -> None
        self.kill()


class Session(object):
    def __init__(
        self,
        sid=None,  # type: Optional[Union[str, uuid.UUID]]
        did=None,  # type: Optional[str]
        timestamp=None,  # type: Optional[datetime]
        started=None,  # type: Optional[datetime]
        duration=None,  # type: Optional[float]
        status=None,  # type: Optional[SessionStatus]
        release=None,  # type: Optional[str]
        environment=None,  # type: Optional[str]
        user=None,  # type: Optional[Any]
        hub=None,  # type: Optional[Hub]
    ):
        # type: (...) -> None
        self._hub = weakref(hub)
        if sid is None:
            sid = uuid.uuid4()
        if started is None:
            started = datetime.utcnow()
        if status is None:
            status = "ok"
        self.seq = 0
        self.started = started
        self.update(
            sid=sid,
            did=did,
            timestamp=timestamp,
            duration=duration,
            release=release,
            environment=environment,
            user=user,
        )

    def update(
        self,
        sid=None,  # type: Optional[Union[str, uuid.UUID]]
        did=None,  # type: Optional[str]
        timestamp=None,  # type: Optional[datetime]
        duration=None,  # type: Optional[float]
        status=None,  # type: Optional[SessionStatus]
        release=None,  # type: Optional[str]
        environment=None,  # type: Optional[str]
        user=None,  # type: Optional[Any]
    ):
        # type: (...) -> None
        if user is not None and did is None:
            did = user.get("id") or user.get("email") or user.get("username")
        if sid is not None:
            self.sid = _make_uuid(sid)
        if did is not None:
            self.did = did
        if timestamp is None:
            timestamp = datetime.utcnow()
        self.timestamp = timestamp
        if duration is not None:
            self.duration = duration
        if status is not None:
            self.status = status
        if release is not None:
            self.release = release
        if environment is not None:
            self.environment = environment

        # any session update bumps this
        self.seq += 1

        # propagate session changes to the hub
        hub = self._hub()
        if hub is not None:
            client = hub.client
            if client is not None:
                client.capture_session(self)

    def close(
        self, status=None  # type: Optional[SessionStatus]
    ):
        # type: (...) -> Any
        if status is None and self.status == "ok":
            status = "exited"
        if status is not None:
            self.update(status=status)

    def mark_failed(
        self, status  # type: SessionStatus
    ):
        # type: (...) -> Any
        if status == "crashed":
            if self.status != "crashed":
                self.update(status="crashed")
        elif status == "degraded":
            if self.status not in ("degraded", "crashed", "abnormal"):
                self.update(status="degraded")

    def to_json(self):
        # type: (...) -> Any
        rv = {
            "sid": str(self.sid),
            "started": _timestamp(self.started),
            "timestamp": _timestamp(self.timestamp),
            "status": self.status,
        }  # type: Dict[str, Any]
        attrs = {}
        if self.did is not None:
            rv["did"] = self.did
        if self.duration is not None:
            rv["duration"] = self.duration
        if self.release is not None:
            attrs["release"] = self.release
        if self.environment is not None:
            attrs["environment"] = self.environment
        if attrs:
            rv["attrs"] = attrs
        return rv
