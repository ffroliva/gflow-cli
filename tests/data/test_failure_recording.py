"""Failure recording (#341): FAILED operation rows, taxonomy, and redaction."""

import hashlib
from pathlib import Path

import structlog

from gflow_cli.api.image import Aspect, GenerateImageRequest, Model
from gflow_cli.api.video import GenerateVideoRequest, Mode, VideoStarted
from gflow_cli.data.models import OperationKind, OperationStatus
from gflow_cli.data.recorder import (
    OperationRecorder,
    _classify_failure,
    record_failed_operation_safe,
)
from gflow_cli.data.redaction import ERROR_DETAIL_MAX_CHARS, redact_error_detail
from gflow_cli.data.repository import DataRepository
from gflow_cli.data.store import DataStore
from gflow_cli.errors import ContentPolicyError, DataStoreError, WafRejectionError


def _fetch_operations(store: DataStore) -> list[dict[str, object]]:
    rows = store.conn.execute(
        "SELECT command, mode, status, error_type, error_detail, prompt, prompt_hash, "
        "prompt_redacted, model, aspect_ratio, completed_at FROM operations"
    ).fetchall()
    cols = (
        "command",
        "mode",
        "status",
        "error_type",
        "error_detail",
        "prompt",
        "prompt_hash",
        "prompt_redacted",
        "model",
        "aspect_ratio",
        "completed_at",
    )
    return [dict(zip(cols, row, strict=True)) for row in rows]


# ---------------------------------------------------------------------------
# redact_error_detail
# ---------------------------------------------------------------------------


def test_redact_error_detail_scrubs_bearer_token() -> None:
    scrubbed = redact_error_detail("HTTP 403: denied for Bearer ya29.a0Af-secret123 token")
    assert "ya29" not in scrubbed
    assert "<redacted:secret>" in scrubbed


def test_redact_error_detail_scrubs_sapisidhash_and_cookies() -> None:
    scrubbed = redact_error_detail(
        "auth SAPISIDHASH 1234_deadbeef failed; cookie __Secure-next-auth.session-token=abc.def"
    )
    assert "deadbeef" not in scrubbed
    assert "abc.def" not in scrubbed


def test_redact_error_detail_scrubs_signed_urls() -> None:
    scrubbed = redact_error_detail(
        "download failed: https://cdn.example/media.png?X-Goog-Signature=abcd1234"
    )
    assert "abcd1234" not in scrubbed
    assert "<redacted:url>" in scrubbed


def test_redact_error_detail_truncates() -> None:
    assert len(redact_error_detail("x" * 2000)) == ERROR_DETAIL_MAX_CHARS


# ---------------------------------------------------------------------------
# _classify_failure taxonomy
# ---------------------------------------------------------------------------


def test_classify_gflow_error_uses_problem_type_slug() -> None:
    error_type, detail = _classify_failure(WafRejectionError("blocked", status=403))
    assert error_type == "waf-rejection"
    assert detail == "blocked"


def test_classify_non_gflow_error_hashes_message() -> None:
    error_type, detail = _classify_failure(RuntimeError("secret123"))
    assert error_type == "RuntimeError"
    assert detail is not None
    assert "secret123" not in detail
    expected = hashlib.sha256(b"secret123").hexdigest()
    assert detail == f"sha256:{expected}"


# ---------------------------------------------------------------------------
# record_failed_operation — insert path (images / no prior row)
# ---------------------------------------------------------------------------


def test_record_failed_operation_inserts_failed_row_with_request_metadata(
    tmp_path: Path,
) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")
        req = GenerateImageRequest(prompt="a red fox", aspect=Aspect.PORTRAIT, model=Model.NARWHAL)
        recorder.record_failed_operation(
            profile_name="default",
            profile_dir=tmp_path / "profile_default",
            command="image t2i",
            mode=OperationKind.T2I,
            exc=WafRejectionError("blocked by WAF", status=403),
            request=req,
        )
        (op,) = _fetch_operations(store)
        assert op["status"] == OperationStatus.FAILED.value
        assert op["error_type"] == "waf-rejection"
        assert op["error_detail"] == "blocked by WAF"
        assert op["command"] == "image t2i"
        assert op["mode"] == "t2i"
        assert op["prompt"] == "a red fox"
        assert op["model"] == Model.NARWHAL.value
        assert op["completed_at"] is not None


def test_record_failed_operation_honors_redacted_prompt_mode(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="redacted")
        req = GenerateImageRequest(prompt="a refused prompt")
        recorder.record_failed_operation(
            profile_name="default",
            profile_dir=tmp_path / "p",
            command="image t2i",
            mode=OperationKind.T2I,
            exc=ContentPolicyError("policy rejection"),
            request=req,
        )
        (op,) = _fetch_operations(store)
        assert op["error_type"] == "content-policy"
        assert op["prompt"] is None
        assert op["prompt_redacted"] == 1
        assert op["prompt_hash"] is not None


def test_record_failed_operation_scrubs_secret_in_detail(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")
        recorder.record_failed_operation(
            profile_name="default",
            profile_dir=tmp_path / "p",
            command="image t2i",
            mode=OperationKind.T2I,
            exc=WafRejectionError("denied Bearer ya29.topsecret", status=403),
        )
        (op,) = _fetch_operations(store)
        detail = op["error_detail"]
        assert isinstance(detail, str)
        assert "topsecret" not in detail


def test_record_failed_operation_without_request(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")
        recorder.record_failed_operation(
            profile_name="default",
            profile_dir=tmp_path / "p",
            command="video t2v",
            mode=OperationKind.T2V,
            exc=RuntimeError("boom"),
        )
        (op,) = _fetch_operations(store)
        assert op["status"] == OperationStatus.FAILED.value
        assert op["error_type"] == "RuntimeError"
        assert op["prompt"] is None


# ---------------------------------------------------------------------------
# record_failed_operation — video STARTED row update path
# ---------------------------------------------------------------------------


def _started_video(recorder: OperationRecorder, tmp_path: Path) -> GenerateVideoRequest:
    req = GenerateVideoRequest(prompt="a slow pan", mode=Mode.T2V)
    recorder.record_started_video(
        profile_name="default",
        profile_dir=tmp_path / "p",
        request=req,
        started=VideoStarted(media_id="media-1", project_id="proj-1", flow_operation_id="op-1"),
    )
    return req


def test_record_failed_operation_updates_started_video_row(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")
        req = _started_video(recorder, tmp_path)
        recorder.record_failed_operation(
            profile_name="default",
            profile_dir=tmp_path / "p",
            command="video t2v",
            mode=OperationKind.T2V,
            exc=WafRejectionError("poll blocked", status=403),
            request=req,
            flow_media_ids=["media-1"],
        )
        (op,) = _fetch_operations(store)  # updated in place — still exactly one row
        assert op["status"] == OperationStatus.FAILED.value
        assert op["error_type"] == "waf-rejection"
        assert op["completed_at"] is not None


def test_record_failed_operation_inserts_when_no_started_row(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")
        req = GenerateVideoRequest(prompt="early failure", mode=Mode.T2V)
        recorder.record_failed_operation(
            profile_name="default",
            profile_dir=tmp_path / "p",
            command="video t2v",
            mode=OperationKind.T2V,
            exc=WafRejectionError("pre-submit block", status=403),
            request=req,
            flow_media_ids=["media-never-inserted"],
        )
        (op,) = _fetch_operations(store)
        assert op["status"] == OperationStatus.FAILED.value


def test_record_failed_operation_never_downgrades_succeeded_row(tmp_path: Path) -> None:
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")
        req = _started_video(recorder, tmp_path)
        op_row = store.conn.execute("SELECT id FROM operations").fetchone()
        recorder.repository.update_operation_status(
            op_row[0], OperationStatus.SUCCEEDED, "2026-07-18T00:00:00Z", None, None
        )
        recorder.record_failed_operation(
            profile_name="default",
            profile_dir=tmp_path / "p",
            command="video t2v",
            mode=OperationKind.T2V,
            exc=RuntimeError("late failure"),
            request=req,
            flow_media_ids=["media-1"],
        )
        ops = _fetch_operations(store)
        statuses = sorted(str(op["status"]) for op in ops)
        assert statuses == ["failed", "succeeded"]  # new row inserted, old row untouched


# ---------------------------------------------------------------------------
# record_failed_operation_safe
# ---------------------------------------------------------------------------


def test_record_failed_operation_safe_swallows_data_store_error(tmp_path: Path) -> None:
    class _ExplodingRecorder(OperationRecorder):
        def record_failed_operation(self, **kwargs: object) -> None:  # type: ignore[override]
            raise DataStoreError("db gone")

    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = _ExplodingRecorder(DataRepository(store), prompt_mode="store")
        record_failed_operation_safe(
            recorder,
            logger=structlog.get_logger("test"),
            profile_name="default",
            profile_dir=tmp_path / "p",
            command="image t2i",
            mode=OperationKind.T2I,
            exc=RuntimeError("original"),
        )  # must not raise


def test_record_failed_operation_safe_accepts_none_recorder(tmp_path: Path) -> None:
    record_failed_operation_safe(
        None,
        logger=structlog.get_logger("test"),
        profile_name="default",
        profile_dir=tmp_path / "p",
        command="image t2i",
        mode=OperationKind.T2I,
        exc=RuntimeError("original"),
    )


# ---------------------------------------------------------------------------
# #341 review-round additions
# ---------------------------------------------------------------------------


def test_redact_error_detail_scrubs_bare_sid_and_equals_sapisidhash() -> None:
    scrubbed = redact_error_detail(
        "cookie: sapisid=xyz; SID=g.a000abc; OSID=o1; SAPISIDHASH=169_deadbeef"
    )
    assert "g.a000abc" not in scrubbed
    assert "xyz" not in scrubbed
    assert "o1" not in scrubbed
    assert "deadbeef" not in scrubbed


def test_redact_error_detail_scrubs_uppercase_and_bare_signed_query() -> None:
    scrubbed = redact_error_detail(
        "fail at HTTPS://cdn.example/x?X-Goog-Signature=abc123 and path?expires=999&sig"
    )
    assert "abc123" not in scrubbed
    assert "expires=999" not in scrubbed


def test_all_gflow_error_slugs_unique_and_nonempty() -> None:
    """The error_type vocabulary is derived from problem_type URIs — pin that
    every subclass yields a distinct, non-empty slug so the taxonomy can't
    silently collapse (#341 review)."""
    import gflow_cli.errors as errors_mod
    from gflow_cli.errors import GFlowError

    def _subclasses(cls: type) -> set[type]:
        out: set[type] = set()
        for sub in cls.__subclasses__():
            out.add(sub)
            out |= _subclasses(sub)
        return out

    slugs: dict[str, str] = {}
    for cls in _subclasses(GFlowError):
        if cls.__module__ != errors_mod.__name__:
            continue
        error_type, _ = _classify_failure(cls("x"))
        assert error_type, f"{cls.__name__} produced an empty error_type"
        prior = slugs.get(error_type)
        # Parent/child pairs sharing a URI would collapse — only identical
        # problem_type inheritance (no override) is allowed to collide.
        if prior is not None:
            assert cls.problem_type == getattr(errors_mod, prior).problem_type, (
                f"slug {error_type!r} claimed by both {prior} and {cls.__name__} "
                "with DIFFERENT problem_type URIs"
            )
        slugs[error_type] = cls.__name__


def test_record_failed_operation_updates_all_started_rows_count_gt_1(tmp_path: Path) -> None:
    """count>1 fires on_started per output; a failure must not strand siblings."""
    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")
        req = GenerateVideoRequest(prompt="two outputs", mode=Mode.T2V, count=2)
        for media_id in ("media-a", "media-b"):
            recorder.record_started_video(
                profile_name="default",
                profile_dir=tmp_path / "p",
                request=req,
                started=VideoStarted(media_id=media_id, project_id="proj-1"),
            )
        recorder.record_failed_operation(
            profile_name="default",
            profile_dir=tmp_path / "p",
            command="video t2v",
            mode=OperationKind.T2V,
            exc=WafRejectionError("blocked", status=403),
            request=req,
            flow_media_ids=["media-a", "media-b"],
        )
        statuses = [
            str(r[0]) for r in store.conn.execute("SELECT status FROM operations").fetchall()
        ]
    assert statuses == ["failed", "failed"]  # no stranded STARTED sibling, no extra insert


def test_record_completed_video_records_failed_when_not_succeeded(tmp_path: Path) -> None:
    """A poll that completes with succeeded=False is a FAILED generation (#341
    review) — previously recorded SUCCEEDED and invisible to `data list errors`."""
    from gflow_cli.api.video import VideoResult, VideoStatus

    with DataStore.open(tmp_path / "gflow.db") as store:
        recorder = OperationRecorder(DataRepository(store), prompt_mode="store")
        req = _started_video(recorder, tmp_path)
        result = VideoResult(
            status=VideoStatus(
                media_id="media-1",
                status="MEDIA_GENERATION_STATUS_FAILED",
                failure_reasons=("PUBLIC_ERROR_UNSAFE_GENERATION",),
            ),
            local_path=None,
            project_id="proj-1",
            flow_operation_id="op-1",
        )
        recorder.record_completed_video(
            profile_name="default",
            _profile_dir=tmp_path / "p",
            request=req,
            result=result,
        )
        (op,) = _fetch_operations(store)
        assert op["status"] == OperationStatus.FAILED.value
        assert op["error_type"] == "generation-failed"
        assert op["error_detail"] == "PUBLIC_ERROR_UNSAFE_GENERATION"
