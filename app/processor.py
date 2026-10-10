import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from app.directory_client import DirectoryClient, DirectoryError
from app.schemas import BulkJob, HospitalResult, JobStatus, RowStatus

logger = logging.getLogger(__name__)

Step = Callable[[BulkJob], Awaitable[None]]


class BulkProcessor:
    def __init__(self, client: DirectoryClient, concurrency: int):
        self._client = client
        self._slots = asyncio.Semaphore(concurrency)

    async def run(self, job: BulkJob) -> BulkJob:
        return await self._execute(job, self._create_and_activate)

    async def resume(self, job: BulkJob) -> BulkJob:
        return await self._execute(job, self._resume)

    async def rollback(self, job: BulkJob) -> BulkJob:
        return await self._execute(job, self._rollback)

    async def _execute(self, job: BulkJob, step: Step) -> BulkJob:
        started = time.perf_counter()
        try:
            await step(job)
        except DirectoryError as exc:
            job.status, job.error = JobStatus.FAILED, str(exc)
        except Exception:
            logger.exception("batch %s crashed", job.batch_id)
            job.status, job.error = JobStatus.FAILED, "unexpected error; see server logs"
        finally:
            job.processing_time_seconds = round(time.perf_counter() - started, 3)
            job.notify()
        logger.info("batch %s finished as %s", job.batch_id, job.status)
        return job

    async def _create_and_activate(self, job: BulkJob) -> None:
        pending = job.with_status(RowStatus.PENDING)
        await asyncio.gather(*(self._create(job, hospital) for hospital in pending))

        if job.failed_hospitals:
            job.status = JobStatus.FAILED
            job.error = f"{job.failed_hospitals} hospital(s) failed; batch left inactive"
            return

        await self._client.activate_batch(job.batch_id)
        for hospital in job.hospitals:
            hospital.mark(RowStatus.CREATED_AND_ACTIVATED)
        job.status, job.batch_activated, job.error = JobStatus.COMPLETED, True, None

    async def _create(self, job: BulkJob, hospital: HospitalResult) -> None:
        async with self._slots:
            try:
                hospital_id = await self._client.create_hospital(hospital, job.batch_id)
            except DirectoryError as exc:
                hospital.mark_failed(str(exc))
            else:
                hospital.mark_created(hospital_id)
            job.notify()

    async def _resume(self, job: BulkJob) -> None:
        await self._adopt_orphans(job)
        for hospital in job.with_status(RowStatus.FAILED):
            hospital.mark(RowStatus.PENDING)
        await self._create_and_activate(job)

    async def _adopt_orphans(self, job: BulkJob) -> None:
        """A create that timed out may still have succeeded upstream; adopt it, don't duplicate."""
        failed = job.with_status(RowStatus.FAILED)
        if not failed:
            return

        known = {h.hospital_id for h in job.hospitals if h.hospital_id is not None}
        orphans = [h for h in await self._client.list_batch(job.batch_id) if h.id not in known]

        for hospital in failed:
            match = next((o for o in orphans if o.matches(hospital)), None)
            if match:
                orphans.remove(match)
                hospital.mark_created(match.id)

    async def _rollback(self, job: BulkJob) -> None:
        await self._client.delete_batch(job.batch_id)
        for hospital in job.hospitals:
            hospital.mark(RowStatus.ROLLED_BACK)
        job.status, job.batch_activated, job.error = JobStatus.ROLLED_BACK, False, None
