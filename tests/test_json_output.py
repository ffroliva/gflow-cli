"""Unit tests for the `--json` payload builders (pure, no I/O)."""

from __future__ import annotations

from pathlib import Path

from gflow_cli import json_output
from gflow_cli.api.dto import GeneratedImage
from gflow_cli.api.video import (
    Aspect,
    GenerateVideoRequest,
    Mode,
    VideoModel,
    VideoResult,
    VideoStatus,
)
from gflow_cli.errors import (
    ContentPolicyError,
    FlowAgentUiError,
    FlowAppError,
    WafRejectionError,
)


def _img(media_name: str = "img-1") -> GeneratedImage:
    return GeneratedImage(
        media_name=media_name,
        workflow_id="wf-1",
        seed=42,
        prompt="a cat",
        model_name_type="NARWHAL",
        aspect_ratio="IMAGE_ASPECT_RATIO_PORTRAIT",
        fife_url="https://flow/x?Signature=abc",
        dimensions=(768, 1344),
    )


class TestImageResult:
    def test_complete_shape(self) -> None:
        payload = json_output.image_result(
            command="image t2i",
            project_id="proj-1",
            model="NARWHAL",
            images=[_img("a"), _img("b")],
            saved_paths=[Path("out/a.png"), Path("out/b.png")],
        )
        assert payload["status"] == "ok"
        assert payload["command"] == "image t2i"
        assert payload["project_id"] == "proj-1"
        assert payload["count"] == 2
        assert "ref_count" not in payload  # t2i omits it
        # Every GeneratedImage field must be surfaced — nothing dropped.
        assert payload["images"][0] == {
            "media_name": "a",
            "workflow_id": "wf-1",
            "seed": 42,
            "prompt": "a cat",
            "model_name_type": "NARWHAL",
            "aspect_ratio": "IMAGE_ASPECT_RATIO_PORTRAIT",
            "dimensions": {"width": 768, "height": 1344},
            "fife_url": "https://flow/x?Signature=abc",
            "is_signed_url": True,
            "local_path": str(Path("out/a.png")),
        }

    def test_i2i_includes_ref_count(self) -> None:
        payload = json_output.image_result(
            command="image i2i",
            project_id="proj-1",
            model="IMAGEN_3_5",
            images=[_img()],
            saved_paths=[Path("out/a.png")],
            ref_count=3,
        )
        assert payload["ref_count"] == 3


class TestVideoResult:
    def _req(self) -> GenerateVideoRequest:
        return GenerateVideoRequest(
            prompt="move",
            mode=Mode.I2V,
            aspect=Aspect.PORTRAIT,
            model=VideoModel.VEO_3_1_FAST,
            duration=8,
            count=1,
            start_image=Path("hero.png"),
        )

    def test_success_shape(self, tmp_path: Path) -> None:
        out = tmp_path / "v.mp4"
        result = VideoResult(
            status=VideoStatus(media_id="m-1", status="MEDIA_GENERATION_STATUS_SUCCESSFUL"),
            local_path=out,
        )
        payload = json_output.video_result(command="video i2v", request=self._req(), result=result)
        assert payload["status"] == "ok"
        assert payload["succeeded"] is True
        assert payload["media_id"] == "m-1"
        assert payload["local_path"] == str(out)
        assert payload["request"]["model"] == "veo_3_1_fast"
        assert payload["request"]["mode"] == "i2v"
        assert payload["request"]["duration"] == 8

    def test_failure_shape(self) -> None:
        result = VideoResult(
            status=VideoStatus(
                media_id="m-2",
                status="MEDIA_GENERATION_STATUS_FAILED",
                failure_reasons=("policy",),
                error_message="blocked",
            ),
            local_path=None,
        )
        payload = json_output.video_result(command="video t2v", request=self._req(), result=result)
        assert payload["status"] == "fail"
        assert payload["succeeded"] is False
        assert payload["local_path"] is None
        assert payload["failure_reasons"] == ["policy"]
        assert payload["error_message"] == "blocked"

    def test_model_none_serializes_as_null(self) -> None:
        req = GenerateVideoRequest(prompt="x", mode=Mode.T2V)  # model None -> Flow UI default
        result = VideoResult(
            status=VideoStatus(media_id="m", status="MEDIA_GENERATION_STATUS_SUCCESSFUL"),
            local_path=Path("v.mp4"),
        )
        payload = json_output.video_result(command="video t2v", request=req, result=result)
        assert payload["request"]["model"] is None


class TestErrorPayload:
    def test_retryable_waf(self) -> None:
        payload = json_output.error_payload(WafRejectionError("blocked"))
        assert payload["status"] == "fail"
        assert payload["error"]["class"] == "WafRejectionError"
        assert payload["error"]["retryable"] is True
        assert payload["error"]["exit_code"] == 10
        assert payload["error"]["title"]  # RFC 9457 fields carried through

    def test_terminal_content_policy_not_retryable(self) -> None:
        payload = json_output.error_payload(ContentPolicyError("nope"))
        assert payload["error"]["class"] == "ContentPolicyError"
        assert payload["error"]["retryable"] is False
        assert payload["error"]["exit_code"] == 5

    def test_flow_app_and_agent_ui_errors_retryable(self) -> None:
        """§6.5 retryable-contract correction (S24): both cohort/crash errors are
        documented retryable and the JSON surface must agree."""
        app = json_output.error_payload(FlowAppError("Flow web app crashed"))
        assert app["error"]["retryable"] is True
        assert app["error"]["exit_code"] == 31
        agent = json_output.error_payload(FlowAgentUiError("agentic cohort"))
        assert agent["error"]["retryable"] is True
        assert agent["error"]["exit_code"] == 25

    def test_retryable_derives_from_shared_classification(self) -> None:
        """CLI JSON must key off errors.is_retryable — no private drift list (S24)."""
        from gflow_cli.errors import is_retryable

        for exc in (
            FlowAppError("x"),
            FlowAgentUiError("x"),
            WafRejectionError("x"),
            ContentPolicyError("x"),
        ):
            assert json_output.error_payload(exc)["error"]["retryable"] is is_retryable(exc)

    def test_unexpected_is_privacy_safe_by_default(self) -> None:
        payload = json_output.unexpected_payload()
        assert payload["error"]["class"] == "UnexpectedError"
        assert payload["error"]["retryable"] is False
        assert payload["error"]["exit_code"] == 1
        # The raw exception message/stack must never leak into the payload
        # unless the caller explicitly opts in via debug=.
        assert "detail" not in payload["error"]
        assert "traceback" not in payload["error"]

    def test_unexpected_with_debug_includes_detail_and_traceback(self) -> None:
        try:
            raise ValueError("boom")
        except ValueError as exc:
            payload = json_output.unexpected_payload(debug=exc)
        assert payload["error"]["detail"] == "boom"
        assert "ValueError" in payload["error"]["traceback"]
        assert "boom" in payload["error"]["traceback"]
        # Non-debug fields stay identical to the default shape.
        assert payload["error"]["class"] == "UnexpectedError"
        assert payload["error"]["exit_code"] == 1
