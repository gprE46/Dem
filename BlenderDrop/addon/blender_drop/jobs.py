"""Thread-safe job store and import queue (no bpy).

The HTTP threads create jobs and push them to ``import_queue``.
The Blender main thread (bpy.app.timers) pops them and performs the import.
Both sides update job states through ``JobStore`` which is protected by a lock.
"""

import queue
import threading
import time
import uuid
from collections import OrderedDict

# Job states (shared with the web UI)
UPLOADING = "UPLOADING"
UPLOADED = "UPLOADED"
QUEUED = "QUEUED"
IMPORTING = "IMPORTING"
IMPORTED = "IMPORTED"
ERROR = "ERROR"

FINAL_STATES = (IMPORTED, ERROR)


class Job:
    __slots__ = (
        "id", "filename", "size", "state", "message", "error_code",
        "created", "updated", "path", "directory", "client", "details",
    )

    def __init__(self, filename, client=""):
        self.id = uuid.uuid4().hex[:12]
        self.filename = filename
        self.size = 0
        self.state = UPLOADING
        self.message = "Receiving file"
        self.error_code = ""
        self.created = time.time()
        self.updated = self.created
        self.path = ""        # final path of the received file
        self.directory = ""   # per-job folder
        self.client = client
        self.details = ""     # e.g. "3 objects"

    def to_dict(self):
        return {
            "id": self.id,
            "filename": self.filename,
            "size": self.size,
            "state": self.state,
            "message": self.message,
            "error_code": self.error_code,
            "details": self.details,
            "created": self.created,
            "updated": self.updated,
        }


class JobStore:
    """In-memory history of the most recent jobs."""

    def __init__(self, max_jobs=50):
        self._lock = threading.Lock()
        self._jobs = OrderedDict()
        self._max = max_jobs
        self._version = 0

    @property
    def version(self):
        """Incremented on every change; lets the UI know when to redraw."""
        return self._version

    def create(self, filename, client=""):
        job = Job(filename, client)
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > self._max:
                self._jobs.popitem(last=False)
            self._version += 1
        return job

    def update(self, job_id, **fields):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            for key, value in fields.items():
                setattr(job, key, value)
            job.updated = time.time()
            self._version += 1
            return job.to_dict()

    def get(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            return job.to_dict() if job else None

    def get_job(self, job_id):
        """Raw Job object (main thread / server internal use)."""
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit=20):
        with self._lock:
            jobs = list(self._jobs.values())[-limit:]
        return [j.to_dict() for j in reversed(jobs)]

    def clear(self):
        with self._lock:
            self._jobs.clear()
            self._version += 1


def new_queue():
    return queue.Queue()
