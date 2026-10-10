import asyncio
from collections.abc import AsyncIterator
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, PrivateAttr, computed_field


class JobConflictError(Exception):
    pass


class HospitalRow(BaseModel):
    row: int
    name: str
    address: str
    phone: str | None = None


class RowError(BaseModel):
    row: int | None = Field(description="Data row number; null for file-level errors")
    message: str


class ValidationReport(BaseModel):
    valid: bool
    total_rows: int
    rows: list[HospitalRow]
    errors: list[RowError]


class UpstreamHospital(BaseModel):
    id: int
    name: str
    address: str
    phone: str | None = None

    def matches(self, row: HospitalRow) -> bool:
        return (self.name, self.address, self.phone) == (row.name, row.address, row.phone)


class RowStatus(StrEnum):
    PENDING = "pending"
    CREATED = "created"
    CREATED_AND_ACTIVATED = "created_and_activated"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class JobStatus(StrEnum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class HospitalResult(HospitalRow):
    status: RowStatus = RowStatus.PENDING
    hospital_id: int | None = None
    error: str | None = None

    def mark_created(self, hospital_id: int) -> None:
        self.hospital_id, self.status, self.error = hospital_id, RowStatus.CREATED, None

    def mark_failed(self, error: str) -> None:
        self.status, self.error = RowStatus.FAILED, error

    def mark(self, status: RowStatus) -> None:
        self.status, self.error = status, None


class BulkJob(BaseModel):
    batch_id: UUID = Field(default_factory=uuid4)
    status: JobStatus = JobStatus.PROCESSING
    batch_activated: bool = False
    processing_time_seconds: float = 0.0
    error: str | None = None
    hospitals: list[HospitalResult]

    _changed: asyncio.Event = PrivateAttr(default_factory=asyncio.Event)

    @classmethod
    def from_rows(cls, rows: list[HospitalRow]) -> "BulkJob":
        return cls(hospitals=[HospitalResult(**row.model_dump()) for row in rows])

    @computed_field
    @property
    def total_hospitals(self) -> int:
        return len(self.hospitals)

    @computed_field
    @property
    def processed_hospitals(self) -> int:
        done = {RowStatus.CREATED, RowStatus.CREATED_AND_ACTIVATED}
        return sum(h.status in done for h in self.hospitals)

    @computed_field
    @property
    def failed_hospitals(self) -> int:
        return sum(h.status is RowStatus.FAILED for h in self.hospitals)

    def claim(self, *allowed: JobStatus) -> None:
        if self.status not in allowed:
            raise JobConflictError(f"batch {self.batch_id} is {self.status}")
        self.status = JobStatus.PROCESSING
        self.notify()

    @property
    def finished(self) -> bool:
        return self.status is not JobStatus.PROCESSING

    def notify(self) -> None:
        self._changed.set()
        self._changed = asyncio.Event()

    async def updates(self) -> AsyncIterator["BulkJob"]:
        while True:
            changed = self._changed
            yield self
            if self.finished:
                return
            await changed.wait()

    def with_status(self, status: RowStatus) -> list[HospitalResult]:
        return [h for h in self.hospitals if h.status is status]
