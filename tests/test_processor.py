import asyncio
from unittest.mock import AsyncMock

import pytest

from app.processor import BulkProcessor
from app.schemas import JobConflictError, JobStatus, RowStatus
from tests.conftest import make_job
from tests.fake_directory import FakeDirectory


def statuses(job) -> list[RowStatus]:
    return [h.status for h in job.hospitals]


async def test_creates_all_rows_then_activates(
    processor: BulkProcessor, directory: FakeDirectory
) -> None:
    job = await processor.run(make_job("A", "B", "C"))

    assert job.status is JobStatus.COMPLETED
    assert job.batch_activated
    assert job.error is None
    assert statuses(job) == [RowStatus.CREATED_AND_ACTIVATED] * 3
    assert (job.processed_hospitals, job.failed_hospitals) == (3, 0)
    assert [h.hospital_id for h in job.hospitals] == [1, 2, 3]
    assert all(h["active"] for h in directory.batch(job.batch_id))


async def test_row_failure_leaves_batch_inactive(
    processor: BulkProcessor, directory: FakeDirectory
) -> None:
    directory.rejected_names.add("B")

    job = await processor.run(make_job("A", "B", "C"))

    assert job.status is JobStatus.FAILED
    assert not job.batch_activated
    assert job.error == "1 hospital(s) failed; batch left inactive"
    assert statuses(job) == [RowStatus.CREATED, RowStatus.FAILED, RowStatus.CREATED]
    assert "rejected by fake directory" in job.hospitals[1].error
    assert not any(h["active"] for h in directory.batch(job.batch_id))


async def test_activation_failure_marks_job_failed(
    processor: BulkProcessor, directory: FakeDirectory
) -> None:
    directory.activation_failures = [500]

    job = await processor.run(make_job("A", "B"))

    assert job.status is JobStatus.FAILED
    assert "activate returned 500" in job.error
    assert statuses(job) == [RowStatus.CREATED] * 2


async def test_respects_concurrency_limit(
    processor: BulkProcessor, directory: FakeDirectory
) -> None:
    directory.delay = 0.01

    job = await processor.run(make_job(*[f"H{i}" for i in range(20)]))

    assert job.status is JobStatus.COMPLETED
    assert directory.peak_in_flight == 4


async def test_processes_rows_concurrently(
    processor: BulkProcessor, directory: FakeDirectory
) -> None:
    directory.delay = 0.05

    job = await processor.run(make_job(*[f"H{i}" for i in range(8)]))

    assert job.processing_time_seconds < 0.05 * 8 / 2


async def test_resume_retries_only_failed_rows(
    processor: BulkProcessor, directory: FakeDirectory
) -> None:
    directory.rejected_names.add("B")
    job = await processor.run(make_job("A", "B", "C"))
    created_ids = [job.hospitals[0].hospital_id, job.hospitals[2].hospital_id]
    directory.rejected_names.clear()

    job.claim(JobStatus.FAILED)
    job = await processor.resume(job)

    assert job.status is JobStatus.COMPLETED
    assert job.batch_activated
    assert [job.hospitals[0].hospital_id, job.hospitals[2].hospital_id] == created_ids
    assert len(directory.batch(job.batch_id)) == 3


async def test_resume_retries_activation_without_recreating(
    processor: BulkProcessor, directory: FakeDirectory
) -> None:
    directory.activation_failures = [500]
    job = await processor.run(make_job("A", "B"))

    job.claim(JobStatus.FAILED)
    job = await processor.resume(job)

    assert job.status is JobStatus.COMPLETED
    assert len(directory.hospitals) == 2


async def test_resume_adopts_rows_created_before_a_timeout(
    processor: BulkProcessor, directory: FakeDirectory
) -> None:
    directory.timeout_after_create.add("B")
    job = await processor.run(make_job("A", "B"))
    assert job.hospitals[1].status is RowStatus.FAILED

    job.claim(JobStatus.FAILED)
    job = await processor.resume(job)

    assert job.status is JobStatus.COMPLETED
    assert len(directory.batch(job.batch_id)) == 2
    assert job.hospitals[1].hospital_id == 2


async def test_resume_still_failing_stays_resumable(
    processor: BulkProcessor, directory: FakeDirectory
) -> None:
    directory.rejected_names.add("B")
    job = await processor.run(make_job("A", "B"))

    job.claim(JobStatus.FAILED)
    job = await processor.resume(job)

    assert job.status is JobStatus.FAILED
    assert len(directory.batch(job.batch_id)) == 1


async def test_rollback_deletes_batch_upstream(
    processor: BulkProcessor, directory: FakeDirectory
) -> None:
    directory.rejected_names.add("B")
    job = await processor.run(make_job("A", "B"))

    job.claim(JobStatus.FAILED)
    job = await processor.rollback(job)

    assert job.status is JobStatus.ROLLED_BACK
    assert statuses(job) == [RowStatus.ROLLED_BACK] * 2
    assert directory.hospitals == {}


async def test_rollback_when_nothing_was_created(
    processor: BulkProcessor, directory: FakeDirectory
) -> None:
    directory.rejected_names.update({"A", "B"})
    job = await processor.run(make_job("A", "B"))

    job.claim(JobStatus.FAILED)
    job = await processor.rollback(job)

    assert job.status is JobStatus.ROLLED_BACK


async def test_unexpected_errors_fail_the_job() -> None:
    client = AsyncMock()
    client.create_hospital.side_effect = RuntimeError("bug")
    processor = BulkProcessor(client, concurrency=2)

    job = await processor.run(make_job("A"))

    assert job.status is JobStatus.FAILED
    assert job.error == "unexpected error; see server logs"


@pytest.mark.parametrize("current", [JobStatus.PROCESSING, JobStatus.ROLLED_BACK])
def test_claim_rejects_disallowed_states(current: JobStatus) -> None:
    job = make_job("A")
    job.status = current

    with pytest.raises(JobConflictError, match=f"is {current}"):
        job.claim(JobStatus.FAILED, JobStatus.COMPLETED)
    assert job.status is current


async def test_updates_stream_progress_until_finished(
    processor: BulkProcessor, directory: FakeDirectory
) -> None:
    directory.delay = 0.01
    job = make_job("A", "B", "C")
    seen: list[tuple[JobStatus, int]] = []

    async def watch() -> None:
        async for snapshot in job.updates():
            seen.append((snapshot.status, snapshot.processed_hospitals))

    watcher = asyncio.create_task(watch())
    await asyncio.sleep(0)
    await processor.run(job)
    await asyncio.wait_for(watcher, timeout=1)

    assert seen[0] == (JobStatus.PROCESSING, 0)
    assert seen[-1] == (JobStatus.COMPLETED, 3)
    assert [count for _, count in seen] == sorted(count for _, count in seen)


async def test_updates_on_finished_job_yield_once(processor: BulkProcessor) -> None:
    job = await processor.run(make_job("A"))

    assert [snapshot.status async for snapshot in job.updates()] == [JobStatus.COMPLETED]


async def test_claim_wakes_watchers(processor: BulkProcessor, directory: FakeDirectory) -> None:
    directory.rejected_names.add("A")
    job = await processor.run(make_job("A"))
    updates = job.updates()
    assert (await anext(updates)).status is JobStatus.FAILED

    job.claim(JobStatus.FAILED)

    assert (await asyncio.wait_for(anext(updates), timeout=1)).status is JobStatus.PROCESSING
