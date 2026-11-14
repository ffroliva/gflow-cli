"""Tests for the stable exit-code core (#1202).

Pins every class in EXIT_CODE_MAP to its legacy and v2 exit codes, and tests CLI
behavior under GFLOW_CLI_EXIT_CODES=2, default (unset), and invalid values.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import structlog
from click.testing import CliRunner

from gflow_cli.cli import main
from gflow_cli.config import get_settings
from gflow_cli.errors import (
    EXIT_CODE_MAP,
    AuthBrowserRejectedError,
    AuthExpiredError,
    AuthLoginTimeoutError,
    AuthMissingError,
    BrowserEngineUnavailableError,
    BrowserSessionClosedError,
    ChainPartialError,
    ConfigurationError,
    ContentPolicyError,
    DataIntegrityError,
    DataMigrationError,
    DataStoreError,
    ExtendUnavailableError,
    FlowAccessUnavailableError,
    FlowAccountChooserError,
    FlowAgentUiError,
    FlowAppError,
    FlowHostMigratedError,
    FrameExtractionError,
    GFlowError,
    InsufficientCreditsError,
    MediaAttributionError,
    MediaUploadRejectedError,
    MentionIndexUnavailableError,
    ModelModeIncompatibilityError,
    NetworkError,
    QueueSchemaError,
    RateLimitError,
    ReferenceNotFoundError,
    SceneConcatError,
    SecurityError,
    ServerCancelUnavailableError,
    SyncPartialError,
    TransportTimeoutError,
    UiModeUnavailableError,
    UiSelectorDriftError,
    UpscaleUnavailableError,
    VideoModelSelectionError,
    WafRejectionError,
    WireFormatError,
    exit_code_for,
    legacy_exit_code_for,
)

#: Complete table of (legacy_code, mode_2_code) for every class in EXIT_CODE_MAP.
#: Mode 2 maps: 4->40, 5->41, 6->42, 7->43, 8->3, 9->44. All other codes unchanged.
EXIT_CODE_TABLE: dict[type[GFlowError], tuple[int, int]] = {
    ChainPartialError: (21, 21),
    FrameExtractionError: (20, 20),
    DataMigrationError: (16, 16),
    DataIntegrityError: (16, 16),
    DataStoreError: (16, 16),
    BrowserSessionClosedError: (15, 15),
    AuthBrowserRejectedError: (14, 14),
    AuthLoginTimeoutError: (12, 12),
    SecurityError: (13, 13),
    AuthMissingError: (8, 3),
    TransportTimeoutError: (9, 44),
    WafRejectionError: (10, 10),
    UpscaleUnavailableError: (22, 22),
    ServerCancelUnavailableError: (22, 22),
    ExtendUnavailableError: (35, 35),
    UiSelectorDriftError: (23, 23),
    InsufficientCreditsError: (37, 37),
    ModelModeIncompatibilityError: (17, 17),
    VideoModelSelectionError: (18, 18),
    BrowserEngineUnavailableError: (24, 24),
    FlowAgentUiError: (25, 25),
    FlowAppError: (31, 31),
    FlowHostMigratedError: (36, 36),
    FlowAccountChooserError: (38, 38),
    FlowAccessUnavailableError: (39, 39),
    UiModeUnavailableError: (28, 28),
    MediaAttributionError: (26, 26),
    MediaUploadRejectedError: (27, 27),
    ReferenceNotFoundError: (32, 32),
    MentionIndexUnavailableError: (29, 29),
    QueueSchemaError: (30, 30),
    SyncPartialError: (34, 34),
    ConfigurationError: (11, 11),
    AuthExpiredError: (3, 3),
    RateLimitError: (4, 40),
    ContentPolicyError: (5, 41),
    NetworkError: (6, 42),
    WireFormatError: (7, 43),
    SceneConcatError: (19, 19),
}


def test_all_classes_in_exit_code_map_are_covered_in_table() -> None:
    """A new class added to EXIT_CODE_MAP without a row in the table test must fail."""
    assert set(EXIT_CODE_TABLE.keys()) == set(EXIT_CODE_MAP.keys())


@pytest.mark.parametrize(
    "exc_cls, expected_legacy, expected_v2",
    [(cls, legacy, v2) for cls, (legacy, v2) in EXIT_CODE_TABLE.items()],
    ids=lambda cls: cls.__name__ if isinstance(cls, type) else str(cls),
)
def test_exit_code_for_both_modes(
    exc_cls: type[GFlowError],
    expected_legacy: int,
    expected_v2: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exc = exc_cls.__new__(exc_cls)

    # 1. Unset env var -> default legacy mode
    monkeypatch.delenv("GFLOW_CLI_EXIT_CODES", raising=False)
    get_settings.cache_clear()
    assert legacy_exit_code_for(exc) == expected_legacy
    assert exit_code_for(exc) == expected_legacy

    # 2. Explicit GFLOW_CLI_EXIT_CODES=1 -> legacy mode
    monkeypatch.setenv("GFLOW_CLI_EXIT_CODES", "1")
    get_settings.cache_clear()
    assert legacy_exit_code_for(exc) == expected_legacy
    assert exit_code_for(exc) == expected_legacy

    # 3. Bogus value -> legacy mode (never crashes)
    monkeypatch.setenv("GFLOW_CLI_EXIT_CODES", "invalid_value")
    get_settings.cache_clear()
    assert legacy_exit_code_for(exc) == expected_legacy
    assert exit_code_for(exc) == expected_legacy

    # 4. Opt-in GFLOW_CLI_EXIT_CODES=2 -> stable core mode
    monkeypatch.setenv("GFLOW_CLI_EXIT_CODES", "2")
    get_settings.cache_clear()
    assert legacy_exit_code_for(exc) == expected_legacy
    assert exit_code_for(exc) == expected_v2


def _make_async_raiser(exc: BaseException) -> Any:
    async def _raiser(*_args: Any, **_kwargs: Any) -> None:
        raise exc

    return _raiser


def test_cli_rate_limit_json_exit_codes(
    install_log_capture: structlog.testing.LogCapture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Test CLI command raising RateLimitError with --json under mode 2, default, and bogus."""
    monkeypatch.setattr("gflow_cli.cli_image._resolve_profile", lambda _profile: "test")
    monkeypatch.setattr("gflow_cli.cli_image._make_provider_dir", lambda _name: tmp_path)
    monkeypatch.setattr(
        "gflow_cli.cli_image._run_t2i",
        _make_async_raiser(RateLimitError(detail="rate limited", status=429)),
    )

    runner = CliRunner()

    # 1. GFLOW_CLI_EXIT_CODES=2 opts in to core: exits 40 and JSON carries 40 / 4
    monkeypatch.setenv("GFLOW_CLI_EXIT_CODES", "2")
    get_settings.cache_clear()
    res_v2 = runner.invoke(main, ["image", "t2i", "test prompt", "--json"])
    assert res_v2.exit_code == 40, res_v2.output
    data_v2 = json.loads(res_v2.stdout)
    assert data_v2["error"]["exit_code"] == 40
    assert data_v2["error"]["exit_code_legacy"] == 4

    # 2. Default (unset) -> legacy: exits 4 and JSON carries 4 / 4
    monkeypatch.delenv("GFLOW_CLI_EXIT_CODES", raising=False)
    get_settings.cache_clear()
    res_def = runner.invoke(main, ["image", "t2i", "test prompt", "--json"])
    assert res_def.exit_code == 4, res_def.output
    data_def = json.loads(res_def.stdout)
    assert data_def["error"]["exit_code"] == 4
    assert data_def["error"]["exit_code_legacy"] == 4

    # 3. Bogus value -> behaves as legacy: exits 4 and JSON carries 4 / 4
    monkeypatch.setenv("GFLOW_CLI_EXIT_CODES", "bogus")
    get_settings.cache_clear()
    res_bogus = runner.invoke(main, ["image", "t2i", "test prompt", "--json"])
    assert res_bogus.exit_code == 4, res_bogus.output
    data_bogus = json.loads(res_bogus.stdout)
    assert data_bogus["error"]["exit_code"] == 4
    assert data_bogus["error"]["exit_code_legacy"] == 4


def test_opt_in_is_read_from_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#1202: the opt-in works from .env like every other GFLOW_CLI_* setting."""
    monkeypatch.delenv("GFLOW_CLI_EXIT_CODES", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("GFLOW_CLI_EXIT_CODES=2\n", encoding="utf-8")
    get_settings.cache_clear()
    assert exit_code_for(RateLimitError.__new__(RateLimitError)) == 40


def test_batch_priority_does_not_depend_on_the_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """#1202: out-of-credits (legacy 37) still outranks a network blip (legacy 6 -> 42)."""
    from gflow_cli.image_batch import BatchOutcome, BatchPromptItem, render_image_batch_summary

    prompt = BatchPromptItem(text="p")
    rows = [
        BatchOutcome(
            0, prompt, "fail", exit_code=legacy_exit_code_for(NetworkError.__new__(NetworkError))
        ),
        BatchOutcome(
            1,
            prompt,
            "fail",
            exit_code=legacy_exit_code_for(
                InsufficientCreditsError.__new__(InsufficientCreditsError)
            ),
        ),
    ]
    monkeypatch.setenv("GFLOW_CLI_EXIT_CODES", "2")
    get_settings.cache_clear()
    assert render_image_batch_summary(rows, title="t") == 37
    rows[1] = BatchOutcome(
        1, prompt, "fail", exit_code=legacy_exit_code_for(RateLimitError.__new__(RateLimitError))
    )
    assert render_image_batch_summary(rows, title="t") == 42  # max legacy 6, translated
