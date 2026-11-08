"""Omni 1.2 is explicit; legacy model identities and durations stay stable."""

from pathlib import Path

import pytest

from gflow_cli.api.video import GenerateVideoRequest, Mode, VideoModel, max_duration_for


@pytest.mark.parametrize("seconds", range(3, 16))
def test_omni12_every_integer_duration(seconds: int) -> None:
    model = VideoModel.from_cli("omni-1.2-flash")
    req = GenerateVideoRequest(
        prompt="test",
        mode=Mode.R2V,
        model=model,
        duration=seconds,
        reference_images=(Path("ref.png"),),
    )
    assert req.duration == seconds
    assert model is VideoModel.OMNI_1_2_FLASH
    assert max_duration_for(model) == 15


@pytest.mark.parametrize("model", [None, VideoModel.OMNI_FLASH, VideoModel.VEO_3_1_LITE])
@pytest.mark.parametrize("seconds", [3, 5, 7, 9, 11, 15])
def test_new_durations_require_explicit_new_model(model: VideoModel | None, seconds: int) -> None:
    with pytest.raises(ValueError):
        GenerateVideoRequest(prompt="test", mode=Mode.T2V, model=model, duration=seconds)


def test_legacy_omni_alias_does_not_change() -> None:
    assert VideoModel.from_cli("omni-flash") is VideoModel.OMNI_FLASH


@pytest.mark.parametrize("duration", ["15", 15.0, 3.5, True, False, 2, 16])
def test_sdk_duration_is_an_actual_integer_in_range(duration: object) -> None:
    with pytest.raises(ValueError):
        GenerateVideoRequest(
            prompt="test", mode=Mode.T2V, model=VideoModel.OMNI_1_2_FLASH, duration=duration
        )  # type: ignore[arg-type]


# #1178: on Omni 1.2, Flow submits start+end above 10s WITHOUT the end frame and bills a
# start-only clip (reporter's measurement, Google AI Ultra; unmeasurable on a 10s-max
# account). Refused before any upload, for every way an end frame can be given.
_END_FRAME_FORMS = (
    {"end_image": Path("end.png")},
    {"end_image_ref_name": "End Frame"},
    {"end_image_ref_id": "11111111-1111-4111-8111-111111111111"},
)


@pytest.mark.parametrize("end", _END_FRAME_FORMS)
@pytest.mark.parametrize("seconds", [11, 12, 15])
def test_omni12_end_frame_above_10s_is_refused(end: dict[str, object], seconds: int) -> None:
    with pytest.raises(ValueError, match="end frame") as exc:
        GenerateVideoRequest(
            prompt="test",
            mode=Mode.I2V,
            model=VideoModel.OMNI_1_2_FLASH,
            duration=seconds,
            start_image=Path("start.png"),
            **end,  # type: ignore[arg-type]
        )
    assert "1178" in str(exc.value)


@pytest.mark.parametrize("seconds", [3, 8, 10])
def test_omni12_end_frame_up_to_10s_is_accepted(seconds: int) -> None:
    req = GenerateVideoRequest(
        prompt="test",
        mode=Mode.I2V,
        model=VideoModel.OMNI_1_2_FLASH,
        duration=seconds,
        start_image=Path("start.png"),
        end_image=Path("end.png"),
    )
    assert req.duration == seconds


def test_omni12_start_only_keeps_15s() -> None:
    req = GenerateVideoRequest(
        prompt="test",
        mode=Mode.I2V,
        model=VideoModel.OMNI_1_2_FLASH,
        duration=15,
        start_image=Path("start.png"),
    )
    assert req.duration == 15
