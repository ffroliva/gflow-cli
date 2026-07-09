"""`gflow instructions` command group — persistent Agent-Mode brief cards.

CRUD over a Flow project's **brief**: the set of instruction cards Agent Mode
consults on every generation. All mutations are read-modify-write against the
LIVE server brief (``get_agent_info`` -> mutate -> ``patch_agent_info``), which
is the single source of truth — gflow never caches card state locally (a cache
would silently drift when the web UI edits cards out-of-band). Card ids are
preserved across the cycle so ``--id`` stays valid and enable/disable/rm edit
the *same* card rather than replacing it. Setting up cards is credits-free (a
PATCH to the brief, not a generation).

See ``docs/INSTRUCTIONS.md`` for the approved spec this module implements.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, cast

import click
from rich.console import Console
from rich.table import Table

from gflow_cli import json_output
from gflow_cli._cli_helpers import (
    _make_provider_dir,
    _resolve_profile,
    _validate_project_id,
    run_with_handlers,
)
from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.image import AgentInstruction
from gflow_cli.api.transports import transport_choices
from gflow_cli.config import get_settings

if TYPE_CHECKING:
    from collections.abc import Callable

    from gflow_cli.api.image import ProjectBrief

console = Console()

# Case-insensitive 8-4-4-4-12 hex — Flow's media/generated-asset UUIDs. A bare
# UUID `--ref` is an already-uploaded/generated asset (routed to
# imageReferenceMediaIds); anything else is a local image path (uploaded first)
# or a character id/name. Same shape used by cli_image._UUID_RE /
# image_upscale._UUID_RE — each call-site module keeps its own copy per repo
# convention rather than cross-importing a private name.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

_TRANSPORT_HELP = (
    "Override transport strategy. Falls back to GFLOW_CLI_TRANSPORT env var "
    "or the built-in default."
)
_PROJECT_HELP = (
    "Target Flow project id (REQUIRED). Persistent cards only make sense on a "
    "real project — find the id in the Flow editor URL (.../project/<id>/...)."
)


def _common_options(func: Callable[..., Any]) -> Callable[..., Any]:
    """Attach the shared ``--project`` / ``--profile`` / ``--transport`` options.

    ``--project`` is ``required=True`` so every subcommand fails fast (exit 2)
    when it is omitted — there is no scratch-project fallback for a persistent
    brief. Applied bottom-up (Click decorators stack), so the visible order in
    ``--help`` is project, profile, transport.
    """
    func = click.option(
        "--transport",
        type=click.Choice(transport_choices(), case_sensitive=False),
        default=None,
        help=_TRANSPORT_HELP,
    )(func)
    func = click.option("--profile", default=None, help="Profile name (overrides default).")(func)
    return click.option(
        "--project",
        "project_id",
        required=True,
        callback=_validate_project_id,
        help=_PROJECT_HELP,
    )(func)


def _prepare(profile: str | None) -> tuple[Path, bool]:
    """Resolve the active profile to its provider dir + headless flag.

    Mirrors the profile/auth bootstrap every ``cli_image`` command runs before
    opening a client. Exits 2 (via the shared helpers) when no session exists.
    """
    provider_dir = _make_provider_dir(_resolve_profile(profile))
    return provider_dir, get_settings().headless


def _fail(message: str) -> NoReturn:
    """Print a usage-style error and exit 2 (Click's usage-error code).

    Selection failures (ambiguous / not-found title or ``--id``) and malformed
    ``apply`` files surface from INSIDE the async read-modify-write coroutine,
    where a raised ``click.UsageError`` would be swallowed by
    ``run_with_handlers``' catch-all and mis-mapped to a generic exit 1. Emitting
    the message and raising ``SystemExit(2)`` instead reuses that wrapper's
    explicit ``except SystemExit: raise`` passthrough, so Click still reports the
    conventional usage exit code without a second browser session.
    """
    console.print(f"[red]Error:[/red] {message}")
    raise SystemExit(2)


def build_card(
    *,
    title: str,
    text: str,
    image_ids: list[str],
    char_ids: list[str],
    enabled: bool,
    card_id: str = "",
) -> AgentInstruction:
    """Construct an :class:`AgentInstruction`, mapping its invariant to exit 2.

    ``AgentInstruction.__post_init__`` rejects a card with neither text nor a
    reference; that is user error (an empty ``--text`` and no ``--ref``), so it
    surfaces as a usage failure rather than an unhandled crash.
    """
    try:
        return AgentInstruction(
            text=text,
            enabled=enabled,
            image_media_ids=tuple(image_ids),
            character_ids=tuple(char_ids),
            title=title,
            id=card_id,
        )
    except ValueError as exc:
        _fail(str(exc))


async def classify_refs(
    client: FlowApiClient, project_id: str, refs: tuple[str, ...]
) -> tuple[list[str], list[str]]:
    """Route each ``--ref`` value to (image_media_ids, character_ids).

    Classification order mirrors ``docs/INSTRUCTIONS.md``'s reference table:

    1. a bare media/generated UUID -> ``imageReferenceMediaIds`` verbatim;
    2. an existing local image file -> uploaded via REST (same path as
       ``gflow image upload``), its returned media id -> ``imageReferenceMediaIds``;
    3. anything else -> a character id/name -> ``characterReferenceEntityNames``.

    A UUID is checked first so a generated-asset id is never mistaken for a
    character. Non-path, non-UUID strings (e.g. ``hero-character``) fall through
    to the character bucket without touching the filesystem.
    """
    image_ids: list[str] = []
    char_ids: list[str] = []
    for ref in refs:
        if _UUID_RE.fullmatch(ref):
            image_ids.append(ref)
            continue
        try:
            resolved = Path(ref).resolve(strict=True)
        except (OSError, ValueError):
            # Not a resolvable path (missing file, or an id/name with characters
            # illegal in a path) -> treat as a character reference.
            char_ids.append(ref)
            continue
        if resolved.is_file():
            asset = await client.upload_image(project_id, resolved)
            image_ids.append(asset.name)
        else:
            char_ids.append(ref)
    return image_ids, char_ids


def _select(brief: ProjectBrief, *, title: str | None, card_id: str | None) -> AgentInstruction:
    """Find one card by ``--id`` (exact) or title (case-insensitive, fail-fast).

    Delegates to :meth:`ProjectBrief.find`, translating its ``ValueError``
    (not-found / ambiguous title) into a usage exit (2).
    """
    try:
        if card_id is not None:
            return brief.find(card_id=card_id)
        return brief.find(title=title)
    except ValueError as exc:
        _fail(str(exc))


def _require_one_selector(title: str | None, card_id: str | None) -> None:
    """Enforce that exactly one of positional TITLE / ``--id`` was given.

    Raised in the command body (before ``run_with_handlers``) so Click maps it
    to the conventional usage exit code 2.
    """
    if (title is None) == (card_id is None):
        msg = "Provide exactly one of TITLE or --id."
        raise click.UsageError(msg)


# ---------------------------------------------------------------------------
# Click group
# ---------------------------------------------------------------------------


@click.group()
def instructions() -> None:
    """Manage a project's Agent-Mode brief (persistent instruction cards).

    Cards steer every generation in a project — the agent folds each ENABLED
    card into the prompt. Adding, listing, toggling, and removing cards is
    credits-free. Every subcommand requires ``--project``.
    """


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


@instructions.command(
    "add",
    short_help="Add a persistent instruction card to a project's brief.",
    help=(
        "Add an instruction card to a project's Agent-Mode brief (credits-free).\n\n"
        "\b\n"
        "Each --ref is classified automatically:\n"
        "  local image path -> uploaded, attached as an image reference\n"
        "  asset UUID        -> attached as an image reference\n"
        "  anything else     -> attached as a character reference (id or name)\n\n"
        "\b\n"
        "Examples:\n"
        '  gflow instructions add "Crayon style" --text "flat 2D crayon drawing" --project <id>\n'
        '  gflow instructions add "Hero look" --text "keep the hero on-model" \\\n'
        "      --ref ./refs/mood.png --ref hero-character --project <id>"
    ),
)
@click.argument("title")
@click.option("--text", required=True, help="Guideline text the agent folds into every prompt.")
@click.option(
    "--ref",
    "refs",
    multiple=True,
    help="Reference: local image path, asset UUID, or character id/name (repeatable).",
)
@click.option(
    "--disabled",
    is_flag=True,
    help="Create the card disabled (present in the brief but ignored until enabled).",
)
@_common_options
def add(
    title: str,
    text: str,
    refs: tuple[str, ...],
    disabled: bool,
    project_id: str,
    profile: str | None,
    transport: str | None,
) -> None:
    """Add an instruction card TITLE to the project's brief."""
    provider_dir, headless = _prepare(profile)
    run_with_handlers(
        lambda: _run_add(
            profile_dir=provider_dir,
            headless=headless,
            transport=transport,
            project_id=project_id,
            title=title,
            text=text,
            refs=refs,
            disabled=disabled,
        ),
        cli_command="instructions add",
    )


async def _run_add(
    *,
    profile_dir: Path,
    headless: bool,
    transport: str | None,
    project_id: str,
    title: str,
    text: str,
    refs: tuple[str, ...],
    disabled: bool,
) -> None:
    async with FlowApiClient(
        profile_dir=profile_dir, headless=headless, transport=transport
    ) as client:
        brief = await client.get_agent_info(project_id)
        image_ids, char_ids = await classify_refs(client, project_id, refs)
        card = build_card(
            title=title,
            text=text,
            image_ids=image_ids,
            char_ids=char_ids,
            enabled=not disabled,
        )
        # Send the FULL card set (existing + new): patch_agent_info REPLACES the
        # brief's cards, and existing cards keep their ids via read-modify-write.
        await client.patch_agent_info(project_id, enabled=True, cards=(*brief.cards, card))
    state = "disabled" if disabled else "enabled"
    console.print(
        f"[green]Added[/green] {state} card [bold]{card.resolved_title()}[/bold] "
        f"to project {project_id}."
    )


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@instructions.command(
    "list",
    short_help="List a project's instruction cards.",
    help="List a project's Agent-Mode brief cards (reads the live server brief).",
)
@_common_options
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit a machine-readable JSON result instead of a Rich table.",
)
def list_cards(
    project_id: str,
    profile: str | None,
    transport: str | None,
    as_json: bool,
) -> None:
    """List the instruction cards on the project's brief."""
    provider_dir, headless = _prepare(profile)
    run_with_handlers(
        lambda: _run_list(
            profile_dir=provider_dir,
            headless=headless,
            transport=transport,
            project_id=project_id,
            as_json=as_json,
        ),
        cli_command="instructions list",
        as_json=as_json,
    )


async def _run_list(
    *,
    profile_dir: Path,
    headless: bool,
    transport: str | None,
    project_id: str,
    as_json: bool,
) -> None:
    async with FlowApiClient(
        profile_dir=profile_dir, headless=headless, transport=transport
    ) as client:
        brief = await client.get_agent_info(project_id)
    if as_json:
        json_output.emit(_brief_payload(project_id, brief))
    else:
        _render_brief_table(project_id, brief)


def _brief_payload(project_id: str, brief: ProjectBrief) -> dict[str, Any]:
    """Build the stable ``--json`` shape for ``instructions list``."""
    return {
        "status": "ok",
        "command": "instructions list",
        "project_id": project_id,
        "enabled": brief.enabled,
        "cards": [
            {
                "id": c.id,
                "title": c.resolved_title(),
                "enabled": c.enabled,
                "text": c.text,
                "image_media_ids": list(c.image_media_ids),
                "character_ids": list(c.character_ids),
            }
            for c in brief.cards
        ],
    }


def _render_brief_table(project_id: str, brief: ProjectBrief) -> None:
    """Render the brief's cards as a Rich table."""
    master = "[green]on[/green]" if brief.enabled else "[yellow]off[/yellow]"
    table = Table(title=f"Instruction cards · project {project_id} · agent mode {master}")
    table.add_column("title", overflow="fold")
    table.add_column("id", overflow="fold")
    table.add_column("enabled")
    table.add_column("refs", justify="right")
    table.add_column("text", overflow="fold")
    for c in brief.cards:
        n_refs = len(c.image_media_ids) + len(c.character_ids)
        enabled = "[green]yes[/green]" if c.enabled else "[dim]no[/dim]"
        table.add_row(c.resolved_title(), c.id[:8], enabled, str(n_refs), _preview(c.text))
    console.print(table)
    if not brief.cards:
        console.print("[dim]No instruction cards on this project's brief.[/dim]")


def _preview(text: str) -> str:
    """First line of *text*, capped for a one-row table cell."""
    first = text.splitlines()[0] if text.strip() else ""
    return f"{first[:57]}…" if len(first) > 58 else first  # noqa: PLR2004


# ---------------------------------------------------------------------------
# enable / disable
# ---------------------------------------------------------------------------


def _selector_options(func: Callable[..., Any]) -> Callable[..., Any]:
    """Attach the positional TITLE + ``--id`` selector to a mutating command."""
    func = click.option(
        "--id",
        "card_id",
        default=None,
        help="Select the card by its stable server id instead of by title.",
    )(func)
    return click.argument("title", required=False)(func)


@instructions.command(
    "enable",
    short_help="Enable an instruction card (by title or --id).",
    help="Enable an instruction card so the agent applies it on every generation.",
)
@_selector_options
@_common_options
def enable(
    title: str | None,
    card_id: str | None,
    project_id: str,
    profile: str | None,
    transport: str | None,
) -> None:
    """Enable the card selected by TITLE or --id."""
    _require_one_selector(title, card_id)
    provider_dir, headless = _prepare(profile)
    run_with_handlers(
        lambda: _run_set_enabled(
            profile_dir=provider_dir,
            headless=headless,
            transport=transport,
            project_id=project_id,
            title=title,
            card_id=card_id,
            enabled=True,
        ),
        cli_command="instructions enable",
    )


@instructions.command(
    "disable",
    short_help="Disable an instruction card (by title or --id).",
    help="Disable an instruction card — it stays in the brief but is ignored.",
)
@_selector_options
@_common_options
def disable(
    title: str | None,
    card_id: str | None,
    project_id: str,
    profile: str | None,
    transport: str | None,
) -> None:
    """Disable the card selected by TITLE or --id."""
    _require_one_selector(title, card_id)
    provider_dir, headless = _prepare(profile)
    run_with_handlers(
        lambda: _run_set_enabled(
            profile_dir=provider_dir,
            headless=headless,
            transport=transport,
            project_id=project_id,
            title=title,
            card_id=card_id,
            enabled=False,
        ),
        cli_command="instructions disable",
    )


async def _run_set_enabled(
    *,
    profile_dir: Path,
    headless: bool,
    transport: str | None,
    project_id: str,
    title: str | None,
    card_id: str | None,
    enabled: bool,
) -> None:
    async with FlowApiClient(
        profile_dir=profile_dir, headless=headless, transport=transport
    ) as client:
        brief = await client.get_agent_info(project_id)
        card = _select(brief, title=title, card_id=card_id)
        # Replace the target in place (preserving its id) and PATCH the FULL set;
        # `is` identity is exact — find() returns the very object from brief.cards.
        updated = replace(card, enabled=enabled)
        new_cards = tuple(updated if c is card else c for c in brief.cards)
        await client.patch_agent_info(project_id, enabled=True, cards=new_cards)
    verb = "Enabled" if enabled else "Disabled"
    console.print(f"[green]{verb}[/green] card [bold]{card.resolved_title()}[/bold].")


# ---------------------------------------------------------------------------
# rm
# ---------------------------------------------------------------------------


@instructions.command(
    "rm",
    short_help="Remove an instruction card (by title or --id).",
    help="Remove an instruction card from the project's brief.",
)
@_selector_options
@_common_options
def rm(
    title: str | None,
    card_id: str | None,
    project_id: str,
    profile: str | None,
    transport: str | None,
) -> None:
    """Remove the card selected by TITLE or --id."""
    _require_one_selector(title, card_id)
    provider_dir, headless = _prepare(profile)
    run_with_handlers(
        lambda: _run_rm(
            profile_dir=provider_dir,
            headless=headless,
            transport=transport,
            project_id=project_id,
            title=title,
            card_id=card_id,
        ),
        cli_command="instructions rm",
    )


async def _run_rm(
    *,
    profile_dir: Path,
    headless: bool,
    transport: str | None,
    project_id: str,
    title: str | None,
    card_id: str | None,
) -> None:
    async with FlowApiClient(
        profile_dir=profile_dir, headless=headless, transport=transport
    ) as client:
        brief = await client.get_agent_info(project_id)
        card = _select(brief, title=title, card_id=card_id)
        # Drop the target; send the remaining set (possibly empty -> clears it).
        new_cards = tuple(c for c in brief.cards if c is not card)
        await client.patch_agent_info(project_id, enabled=True, cards=new_cards)
    console.print(f"[green]Removed[/green] card [bold]{card.resolved_title()}[/bold].")


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


@instructions.command(
    "apply",
    short_help="Declaratively full-sync a project's brief from a TOML/JSON file.",
    help=(
        "Replace a project's brief cards with the contents of FILE "
        "(idempotent full-sync).\n\n"
        "\b\n"
        "TOML: one [[card]] table per card with title/text/ref[]/enabled.\n"
        'JSON: an object {"card": [...]} or a bare list of the same entries.\n\n'
        "\b\n"
        "Example:\n"
        "  gflow instructions apply brief.toml --project <id>"
    ),
)
@click.argument(
    "file",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
)
@_common_options
def apply(
    file: Path,
    project_id: str,
    profile: str | None,
    transport: str | None,
) -> None:
    """Full-sync the project's brief from FILE (TOML or JSON)."""
    entries = _load_apply_file(file)
    provider_dir, headless = _prepare(profile)
    run_with_handlers(
        lambda: _run_apply(
            profile_dir=provider_dir,
            headless=headless,
            transport=transport,
            project_id=project_id,
            entries=entries,
        ),
        cli_command="instructions apply",
    )


def _load_apply_file(path: Path) -> list[dict[str, Any]]:
    """Parse FILE into a list of raw card entries (TOML ``[[card]]`` or JSON).

    Detected by suffix: ``.json`` -> JSON, everything else -> TOML. Accepts a
    JSON top-level list as well as an object with a ``card`` array so a hand-
    written brief and a serialized one both load.
    """
    raw = path.read_bytes()
    try:
        data: Any = (
            json.loads(raw)
            if path.suffix.lower() == ".json"
            else tomllib.loads(raw.decode("utf-8"))
        )
    except (tomllib.TOMLDecodeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        _fail(f"could not parse {path.name}: {exc}")

    entries: list[Any] | None = None
    if isinstance(data, list):
        entries = cast("list[Any]", data)
    elif isinstance(data, dict):
        d_dict = cast("dict[str, Any]", data)
        card_val = d_dict.get("card")
        if isinstance(card_val, list):
            entries = cast("list[Any]", card_val)

    if entries is None:
        _fail(f"{path.name} must contain a top-level [[card]] array (or a JSON list of cards).")

    cards: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            _fail(f"each card in {path.name} must be a table/object.")
        cards.append(cast(dict[str, Any], entry))
    return cards


async def _run_apply(
    *,
    profile_dir: Path,
    headless: bool,
    transport: str | None,
    project_id: str,
    entries: list[dict[str, Any]],
) -> None:
    async with FlowApiClient(
        profile_dir=profile_dir, headless=headless, transport=transport
    ) as client:
        cards = await _cards_from_entries(client, project_id, entries)
        # Full replace: the file is the declarative source of truth for the brief.
        await client.patch_agent_info(project_id, enabled=True, cards=cards)
    console.print(f"[green]Applied[/green] {len(cards)} card(s) to project {project_id}'s brief.")


async def _cards_from_entries(
    client: FlowApiClient, project_id: str, entries: list[dict[str, Any]]
) -> tuple[AgentInstruction, ...]:
    """Build instruction cards from parsed ``apply`` entries, classifying refs."""
    cards: list[AgentInstruction] = []
    for entry in entries:
        raw_refs = entry.get("ref", [])
        if not isinstance(raw_refs, list):
            _fail("each card's 'ref' must be a list of strings.")
        refs = tuple(str(r) for r in cast(list[Any], raw_refs))
        image_ids, char_ids = await classify_refs(client, project_id, refs)
        cards.append(
            build_card(
                title=str(entry.get("title", "")),
                text=str(entry.get("text", "")),
                image_ids=image_ids,
                char_ids=char_ids,
                enabled=bool(entry.get("enabled", True)),
            )
        )
    return tuple(cards)


# ---------------------------------------------------------------------------
# toggle-mode
# ---------------------------------------------------------------------------


@instructions.command(
    "toggle-mode",
    short_help="Turn the project's brief master switch on or off.",
    help=(
        "Toggle the brief-level master switch. When off, NO cards apply even if "
        "individually enabled; when on, the agent applies every enabled card."
    ),
)
@click.option(
    "--on/--off",
    "enable_mode",
    default=None,
    help="Turn the brief master switch on (--on) or off (--off).",
)
@_common_options
def toggle_mode(
    enable_mode: bool | None,
    project_id: str,
    profile: str | None,
    transport: str | None,
) -> None:
    """Turn the project's brief master switch --on or --off."""
    if enable_mode is None:
        msg = "Provide --on or --off."
        raise click.UsageError(msg)
    provider_dir, headless = _prepare(profile)
    run_with_handlers(
        lambda: _run_toggle_mode(
            profile_dir=provider_dir,
            headless=headless,
            transport=transport,
            project_id=project_id,
            enable_mode=enable_mode,
        ),
        cli_command="instructions toggle-mode",
    )


async def _run_toggle_mode(
    *,
    profile_dir: Path,
    headless: bool,
    transport: str | None,
    project_id: str,
    enable_mode: bool,
) -> None:
    async with FlowApiClient(
        profile_dir=profile_dir, headless=headless, transport=transport
    ) as client:
        # Master switch only — cards are left untouched (no cards= mask sent).
        await client.patch_agent_info(project_id, enabled=enable_mode)
    state = "on" if enable_mode else "off"
    console.print(f"[green]Agent mode {state}[/green] for project {project_id}.")
