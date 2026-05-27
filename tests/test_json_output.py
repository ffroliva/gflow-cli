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
from gflow_cli.errors import ContentPolicyError, WafRejectionError


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

    def test_unexpected_is_privacy_safe(self) -> None:
        payload = json_output.unexpected_payload()
        assert payload["error"]["class"] == "UnexpectedError"
        assert payload["error"]["retryable"] is False
        assert payload["error"]["exit_code"] == 1
        # The raw exception message/stack must never leak into the payload.
        assert "detail" not in payload["error"]
