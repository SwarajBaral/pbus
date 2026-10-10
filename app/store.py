from uuid import UUID

from app.schemas import BulkJob


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[UUID, BulkJob] = {}

    def add(self, job: BulkJob) -> BulkJob:
        self._jobs[job.batch_id] = job
        return job

    def get(self, batch_id: UUID) -> BulkJob | None:
        return self._jobs.get(batch_id)

    def recent(self) -> list[BulkJob]:
        return list(reversed(self._jobs.values()))
