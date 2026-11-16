"""Reporting, plan rendering, and output formatting helpers for `gflow video` (#1158).

Extracted from cli_video.py to keep the module below file length and complexity limits.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
from rich.console import Console

from gflow_cli import json_output
from gflow_cli._cli_helpers import OutputFormat
from gflow_cli.api.transports import migrated_extend
from gflow_cli.errors import FlowApiError, GFlowError

if TYPE_CHECKING:
    from gflow_cli.api.video import VideoResult

__all__ = [
    "_chain_json",
    "_emit_chain_result",
    "_emit_extend_json_payload",
    "_emit_extend_session_result",
    "_emit_video_result",
    "_free_rerender_hint",
    "_migrated_extend_payload",
    "_print_chain_plan",
    "_print_extend_plan",
    "_report_migrated_extend",
]

_EXTEND_COMMAND = "video extend"
_EXTEND_SEGMENT_SECONDS = 8
_EXTEND_CONTENT_SECONDS = 7


def _emit_video_result(
    command: str,
    request: Any,
    result: VideoResult,
    output_format: str,
    is_json: bool,
    console: Console,
) -> None:
    """Emit video generation result according to format (id, json, or human table)."""
    if output_format == OutputFormat.ID.value:
        if not result.status.succeeded:
            reasons = (
                ", ".join(result.status.failure_reasons)
                or result.status.error_message
                or "unknown reason"
            )
            Console(stderr=True).print(f"[red]Video generation failed:[/red] {reasons}")
            raise SystemExit(1)
        if result.status.media_id:
            click.echo(result.status.media_id)
        return

    if is_json or output_format == OutputFormat.JSON.value:
        json_output.emit(json_output.video_result(command=command, request=request, result=result))
        if not result.status.succeeded:
            raise SystemExit(1)
        return

    if not result.status.succeeded:
        reasons = (
            ", ".join(result.status.failure_reasons)
            or result.status.error_message
            or "unknown reason"
        )
        console.print(f"[red]Video generation failed:[/red] {reasons}")
        raise SystemExit(1)

    console.print(f"[bold green]Saved:[/bold green] {result.local_path}")


def _emit_chain_result(
    results: list[Any],
    chain_id: str,
    partial: bool,
    output_format: str,
    as_json: bool,
    console: Console,
) -> None:
    """Emit video chain results formatted as id list, json, or human table."""
    if output_format == OutputFormat.ID.value:
        for r in results:
            click.echo(r.media_id or chain_id)
        return

    if as_json or output_format == OutputFormat.JSON.value:
        json_output.emit(
            _chain_json(
                chain_id=chain_id,
                results=results,
                partial=partial,
                completed_paths=[r.local_path for r in results],
            )
        )
        return

    console.print(f"[bold green]Chain complete:[/bold green] {len(results)} link(s)")
    for r in results:
        console.print(f"  [{r.index}] {r.local_path}")
    console.print(f"[dim]Stitch into one file with `gflow scene` (chain_id {chain_id}).[/dim]")


def _emit_extend_json_payload(
    payload: dict[str, Any],
    result: Any,
    rendered: str | None,
    model_key: str,
    aspect: str,
    segments: int,
    seed: int | None,
) -> None:
    last_media = result.completed_media_ids[-1] if result.completed_media_ids else None
    outcome: dict[str, str] = {}
    if result.aborted or result.error is not None:
        billed = bool(result.completed_media_ids) or result.credits_spent > 0
        outcome = {"charged": "yes" if billed else "maybe", "next_action": "reconcile"}
        operation_id = getattr(result.error, "operation_id", "")
        if operation_id:
            outcome["operation_id"] = operation_id
    payload.update(
        {
            "status": "ok" if not result.aborted and result.error is None else "fail",
            "command": _EXTEND_COMMAND,
            "media_id": last_media,
            "generation_status": (
                "MEDIA_GENERATION_STATUS_COMPLETED"
                if not result.aborted and result.error is None
                else "MEDIA_GENERATION_STATUS_FAILED"
            ),
            "succeeded": not result.aborted and result.error is None,
            "local_path": rendered,
            **outcome,
            "failure_reasons": [],
            "error_message": str(result.error) if result.error is not None else None,
            "request": {
                "model": model_key,
                "mode": "extend",
                "aspect": aspect,
                "duration": segments * _EXTEND_CONTENT_SECONDS,
                "count": segments,
                "seed": seed,
            },
        }
    )
    json_output.emit(payload)


def _emit_extend_id_result(result: Any) -> None:
    if result.aborted or result.error is not None:
        if result.error is not None:
            raise result.error
        raise SystemExit(1)
    if result.completed_media_ids:
        click.echo(result.completed_media_ids[-1])


def _emit_extend_session_result(  # noqa: PLR0913
    result: Any,
    rendered: str | None,
    profile_name: str,
    project_id: str,
    model_key: str,
    aspect: str,
    segments: int,
    seed: int | None,
    target_scene: str | None,
    rerender: str,
    output_format: str,
    as_json: bool,
    console: Console,
) -> None:
    """Emit extend session outcome and exit cleanly or raise mapped error."""
    if output_format == OutputFormat.ID.value:
        _emit_extend_id_result(result)
        return

    payload: dict[str, Any] = {
        "scene_id": target_scene,
        "project_id": project_id,
        "model": model_key,
        "segments_requested": segments,
        "segments_completed": len(result.completed_media_ids),
        "media_ids": result.completed_media_ids,
        "credits_spent": result.credits_spent,
        "seconds_added": len(result.completed_media_ids) * _EXTEND_CONTENT_SECONDS,
        "seconds_billed": len(result.completed_media_ids) * _EXTEND_SEGMENT_SECONDS,
        "output": rendered,
        "aborted": result.aborted,
        "profile": profile_name,
    }

    if as_json or output_format == OutputFormat.JSON.value:
        _emit_extend_json_payload(payload, result, rendered, model_key, aspect, segments, seed)
    else:
        state = (
            "[yellow]Partial[/yellow]" if result.aborted else "[bold green]Extended[/bold green]"
        )
        console.print(
            f"{state} — {len(result.completed_media_ids)}/{segments} segment(s), "
            f"{result.credits_spent} credits, scene {target_scene}"
        )
        if rendered:
            console.print(f"  wrote: {rendered}")
        elif result.completed_media_ids:
            console.print(f"  render one file: {rerender}")

    if result.error is not None:
        if as_json or output_format == OutputFormat.JSON.value:
            raise SystemExit(json_output.exit_code_for(result.error))
        raise result.error

    if result.aborted:
        raise SystemExit(1)


def _migrated_extend_payload(
    outcome: migrated_extend.ExtendOutcome,
    rendered: str | None,
    *,
    profile_name: str,
    media_id: str,
    project_id: str,
    aspect: str | None,
) -> dict[str, Any]:
    """The ``--json`` result of one migrated extend (``rendered`` is the file, if any)."""
    record = outcome.record
    ok = rendered is not None
    added, billed = _EXTEND_CONTENT_SECONDS, _EXTEND_SEGMENT_SECONDS
    if record.media_id == media_id:
        added = billed = migrated_extend.extend_key_seconds(outcome.model_key)
    return {
        "status": "ok" if ok else "fail",
        "command": _EXTEND_COMMAND,
        "media_id": record.media_id,
        "generation_status": (
            "MEDIA_GENERATION_STATUS_COMPLETED" if ok else "MEDIA_GENERATION_STATUS_FAILED"
        ),
        "succeeded": ok,
        "local_path": rendered,
        "output": rendered,
        "failure_reasons": [],
        "error_message": None if ok else f"Flow reported status {record.status}",
        "scene_id": outcome.scene_id,
        "project_id": project_id,
        "model": outcome.model_key,
        "segments_requested": 1,
        "segments_completed": 1 if ok else 0,
        "media_ids": [record.media_id],
        "credits_spent": None,
        "seconds_added": added if ok else 0,
        "seconds_billed": billed,
        "aborted": not ok,
        "profile": profile_name,
        "host": "flow.google.com",
        **({} if ok else {"charged": "maybe", "next_action": "reconcile"}),
        "request": {
            "model": outcome.model_key,
            "mode": "extend",
            "aspect": outcome.aspect or aspect,
            "duration": added,
            "count": 1,
            "seed": None,
        },
    }


def _report_migrated_extend(  # noqa: PLR0913
    outcome: migrated_extend.ExtendOutcome,
    target: Path | None,
    *,
    profile_name: str,
    media_id: str,
    project_id: str,
    aspect: str | None,
    as_json: bool,
    output_format: str = "table",
    console: Console | None = None,
) -> None:
    """Emit the migrated extend's result, then raise if Flow did not complete it."""
    c = console or Console()
    record = outcome.record
    ok = target is not None
    rendered = str(target) if target is not None else None
    if output_format == OutputFormat.ID.value:
        if not ok:
            raise FlowApiError("Extend failed to render")
        click.echo(record.media_id)
        return
    if as_json or output_format == OutputFormat.JSON.value:
        json_output.emit(
            _migrated_extend_payload(
                outcome,
                rendered,
                profile_name=profile_name,
                media_id=media_id,
                project_id=project_id,
                aspect=aspect,
            )
        )
    else:
        state = "[bold green]Extended[/bold green]" if ok else "[red]Failed[/red]"
        c.print(f"{state} — {record.media_id}, scene {outcome.scene_id or 'unknown'}")
        if rendered:
            c.print(f"  wrote: {rendered}")
    if not ok:
        err = FlowApiError(
            f"extend of {media_id} ended with Flow status {record.status}; it may have been billed",
            route="batchexecute:fZytfe",
        )
        if as_json or output_format == OutputFormat.JSON.value:
            raise SystemExit(json_output.exit_code_for(err))
        raise err


def _print_chain_plan(
    *,
    chain_id: str,
    aspect: str,
    model: Any,
    links: Sequence[Any],
    skipped: int = 0,
    console: Console | None = None,
) -> None:
    """Print human-readable chain plan before execution."""
    c = console or Console()
    remaining = len(links) - skipped
    operation_noun = "operation" if remaining == 1 else "operations"
    c.print(f"[bold]Chain plan[/bold] ([dim]{chain_id}[/dim])")
    c.print(
        f"  {len(links)} link(s), aspect {aspect}, model {model.value}"
        + (f" — {skipped} already completed, {remaining} to generate" if skipped else "")
    )
    c.print(f"  [yellow]{remaining} pending video {operation_noun}[/yellow]")
    c.print(
        "  Video operations may consume credits. Current cost varies by model, duration, "
        "account tier, and Flow policy; check Google Flow before submitting."
    )
    for idx, spec in enumerate(links):
        mode = "t2v" if idx == 0 else "i2v"
        link_model = spec.model.value if spec.model is not None else model.value
        status = " [dim](done)[/dim]" if idx < skipped else ""
        c.print(f"  [{idx}] {mode} · {link_model} · {spec.prompt!r}{status}")


def _print_extend_plan(
    *,
    media_id: str,
    prompt: str,
    aspect: str,
    segments: int,
    migrated: bool = False,
    console: Console | None = None,
) -> None:
    """Show what will be submitted BEFORE a client exists."""
    c = console or Console()
    c.print("[bold]Extend plan[/bold]")
    c.print(f"  source clip : {media_id}")
    c.print(f"  prompt      : {prompt}")
    c.print(f"  aspect      : {aspect}")
    if migrated:
        c.print(
            "  cost        : spends credits — flow.google.com does not report the price "
            "before submitting; compare `gflow credits user` before and after"
        )
        c.print("  length      : set by the key; measured 2026-11-08, the Omni key grows")
        c.print("                the source clip in place by ~4s (Veo: a separate ~7s clip)")
        return
    footage = segments * _EXTEND_CONTENT_SECONDS
    billed = segments * _EXTEND_SEGMENT_SECONDS
    c.print(
        f"  segments    : {segments} x ~{_EXTEND_CONTENT_SECONDS}s = ~{footage}s of new "
        f"footage (Flow bills {billed}s)"
    )
    c.print("  cost        : spends credits — exact amount is shown before submitting")


def _free_rerender_hint(project_id: str, profile: str, clips: list[str], *, resumed: bool) -> str:
    """The `gflow scene create` command that renders this extend run without credits."""
    refs = " ".join(clips) if clips else "<clip-workflow-id> ..."
    command = (
        f"gflow scene create --project {project_id} --profile {profile} {refs} --out-file <path>"
    )
    if resumed:
        return (
            f"{command}. This run resumed a scene: put its earlier clips before these, in "
            "the order and with the trims `gflow scene show` lists."
        )
    return command


def _chain_json(
    *,
    chain_id: str,
    results: list[Any],
    partial: bool,
    completed_paths: list[Path],
    error: GFlowError | None = None,
) -> dict[str, Any]:
    """Machine-readable chain result payload."""
    members = (
        {"charged": error.charged, "next_action": error.resolved_next_action()}
        if error is not None
        else {}
    )
    return {
        **members,
        "status": "fail" if partial else "ok",
        "command": "video chain",
        "chain_id": chain_id,
        "partial": partial,
        "links": [
            {
                "index": r.index,
                "media_id": r.media_id,
                "local_path": str(r.local_path),
            }
            for r in results
        ],
        "completed_paths": [str(p) for p in completed_paths],
    }
