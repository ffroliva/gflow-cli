from __future__ import annotations

import dataclasses
import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

import structlog

from gflow_cli.config import get_settings
from gflow_cli.errors import (
    ConfigurationError,
    DataStoreError,
    GFlowError,
    MentionIndexUnavailableError,
)

if TYPE_CHECKING:
    # Avoid circular imports for type hints
    from gflow_cli.api.client import FlowApiClient

log = structlog.get_logger("mentions")


@dataclass
class MentionToken:
    start_idx: int
    end_idx: int
    candidate_text: str


@dataclass
class IndexEntry:
    id: str
    name: str
    kind: str  # "entity" or "media"
    # False only when we KNOW a character entity has no reference images (its
    # workflow_ids are empty). Such an entity cannot stage as a referenceEntity,
    # so tagging it would fail deep in the UI attach with a cryptic
    # WireFormatError. Defaults True so entries built from shapes that don't
    # carry the info (dict fixtures, media assets) are never falsely rejected —
    # the runtime integrity guard remains the last-resort safety net.
    has_reference_images: bool = True


class AssetIndex:
    def __init__(
        self, entities: list[Any] | None = None, media_assets: list[Any] | None = None
    ) -> None:
        self.entries: list[IndexEntry] = []

        for e in entities or []:
            if hasattr(e, "entity_id") and hasattr(e, "display_name"):
                self.entries.append(
                    IndexEntry(
                        id=str(e.entity_id),
                        name=str(e.display_name),
                        kind="entity",
                        # Character.workflow_ids is empty for a bare, image-less
                        # entity — not taggable.
                        has_reference_images=bool(getattr(e, "workflow_ids", None)),
                    )
                )
            elif isinstance(e, dict):
                d = cast(dict[str, Any], e)
                entity_id = d.get("entityId")
                info = d.get("entityInfo")
                info_dict = cast(dict[str, Any], info) if isinstance(info, dict) else {}
                display_name = info_dict.get("displayName")
                if entity_id and display_name:
                    self.entries.append(
                        IndexEntry(id=str(entity_id), name=str(display_name), kind="entity")
                    )

        for m in media_assets or []:
            if isinstance(m, dict):
                dm = cast(dict[str, Any], m)
                media_id = dm.get("media_id") or dm.get("flow_media_id")
                display_name = dm.get("display_name")
                if media_id and display_name:
                    self.entries.append(
                        IndexEntry(id=str(media_id), name=str(display_name), kind="media")
                    )

    @classmethod
    async def build_for_project(cls, client: FlowApiClient, project_id: str) -> AssetIndex:
        """Build the mention index from the character and media catalog sources.

        Only called once a prompt has been found to contain an ``@mention``
        (see ``resolve_and_apply``) — a mention-free prompt never reaches here,
        so its sources are never queried. A source that loads fine with zero
        rows is a legitimate empty catalog, not an outage, and is returned as
        such. A source that RAISES its documented typed error is an outage:
        fail closed with ``MentionIndexUnavailableError`` (naming which source)
        instead of silently degrading to an empty index, which would make an
        outage indistinguishable from "no matching asset".
        """
        try:
            entities = await client.list_characters(project_id)
        except GFlowError as exc:
            raise MentionIndexUnavailableError(
                detail="the character source is unavailable"
            ) from exc

        settings = get_settings()
        db_path = settings.resolved_db_path()
        try:
            from gflow_cli.data.queries import list_project_media_assets

            media_assets = list_project_media_assets(db_path=db_path, project_id=project_id)
        except DataStoreError as exc:
            raise MentionIndexUnavailableError(detail="the media source is unavailable") from exc

        return cls(entities=entities, media_assets=media_assets)


@dataclass
class ResolvedMention:
    name: str
    kind: str
    id: str
    shadowed: str | None = None


@dataclass
class ResolvedMentions:
    mentions: list[ResolvedMention]
    de_tagged_prompt: str


# A mention is an ``@`` that is NOT escaped (``@@`` → literal ``@``) and NOT
# preceded by a word char (so ``user@example`` is not a mention). Its candidate
# span runs until the next ``.!?,;:`` / newline / ``@``. finditer scans left to
# right and consumes ``@@`` escapes as whole units, so the second ``@`` of a pair
# never starts a mention. Group 1 is present only on the mention branch.
_MENTION_RE = re.compile(r"@@|(?<!\w)@([^.!?,;:\n@]*)")


def parse_mentions(text: str) -> list[MentionToken]:
    tokens: list[MentionToken] = []
    for m in _MENTION_RE.finditer(text):
        candidate = m.group(1)
        if candidate is None:  # matched an "@@" escape, not a mention
            continue
        tokens.append(MentionToken(start_idx=m.start(), end_idx=m.end(), candidate_text=candidate))
    return tokens


def resolve_mentions(
    tokens: list[MentionToken],
    index: AssetIndex,
    *,
    path: str,
    model: str,
    prompt: str = "",
    existing_refs: list[str] | None = None,
) -> ResolvedMentions:
    resolved_list: list[ResolvedMention] = []
    replacements: list[tuple[int, int, str]] = []
    existing_set: set[str] = set(existing_refs or [])
    seen_mention_ids: set[str] = set()

    # Determine reference cap
    if path == "image":
        from gflow_cli.api.image import Model as ImageModel
        from gflow_cli.api.image import reference_cap_for

        try:
            model_enum = ImageModel.from_cli(model)
            cap = reference_cap_for(model_enum)
        except Exception:
            cap = 4
    else:
        from gflow_cli.api.video import VideoModel, reference_cap_for

        try:
            model_enum = VideoModel.from_cli(model)
            cap = reference_cap_for(model_enum) if model_enum is not None else 4
        except Exception:
            cap = 4

    for tok in tokens:
        cand = tok.candidate_text

        # Check @me likeness
        if cand.lower().startswith("me") and (
            len(cand) == 2 or not re.match(r"\w", cand[2], re.UNICODE)
        ):
            log.info("mention_unresolved", name="me")
            raise ConfigurationError(
                detail="avatar likeness is region-gated / not supported yet",
                remediation_hint="likeness:checkEligibility returned REGION for our accounts.",
            )

        # Greedily match longest name in the index
        matches: list[IndexEntry] = []
        for entry in index.entries:
            name_len = len(entry.name)
            if cand.lower().startswith(entry.name.lower()):
                # Word boundary check
                if len(cand) == name_len or not re.match(r"\w", cand[name_len], re.UNICODE):
                    matches.append(entry)

        matched_entry: IndexEntry | None = None
        shadowed_id: str | None = None

        if matches:
            # Sort by length descending, so longest match is first
            matches.sort(key=lambda m: len(m.name), reverse=True)
            max_len = len(matches[0].name)
            best_matches: list[IndexEntry] = [m for m in matches if len(m.name) == max_len]

            entities_matched: list[IndexEntry] = [m for m in best_matches if m.kind == "entity"]
            media_matched: list[IndexEntry] = [m for m in best_matches if m.kind == "media"]

            if entities_matched:
                if len(entities_matched) > 1:
                    log.info("mention_unresolved", name=entities_matched[0].name)
                    ids_str = ", ".join(ent.id for ent in entities_matched)
                    raise ConfigurationError(
                        detail=(
                            f"Ambiguous mention of '{entities_matched[0].name}': "
                            f"multiple entity assets found with ids: {ids_str}"
                        )
                    )
                matched_entry = entities_matched[0]
                if media_matched:
                    shadowed_id = media_matched[0].id
            else:
                if len(media_matched) > 1:
                    log.info("mention_unresolved", name=media_matched[0].name)
                    ids_str = ", ".join(ent.id for ent in media_matched)
                    raise ConfigurationError(
                        detail=(
                            f"Ambiguous mention of '{media_matched[0].name}': "
                            f"multiple media assets found with ids: {ids_str}"
                        )
                    )
                matched_entry = media_matched[0]

        if matched_entry is None:
            m = re.match(r"^(\w[\w-]*)", cand, re.UNICODE)
            unknown_name = m.group(1) if m else cand

            def strip_ansi(s: str) -> str:
                return re.sub(r"\x1b\[[0-9;]*m", "", s)

            available_names = sorted({strip_ansi(ent.name) for ent in index.entries})
            available_str = ", ".join(available_names) if available_names else "<none>"

            log.info("mention_unresolved", name=unknown_name)
            raise ConfigurationError(
                detail=f"Unknown mention '@{unknown_name}'. Available assets: {available_str}"
            )

        if matched_entry.kind == "media" and path == "video":
            log.info("mention_unresolved", name=matched_entry.name)
            raise ConfigurationError(
                detail=(
                    f"media mentions on the video path are Phase 3 (found '@{matched_entry.name}')"
                )
            )

        if not re.match(r"^[a-zA-Z0-9_-]+$", matched_entry.id):
            log.info("mention_unresolved", name=matched_entry.name)
            raise ConfigurationError(
                detail=(
                    f"Invalid asset ID format resolved for mention "
                    f"'@{matched_entry.name}': {matched_entry.id}"
                )
            )

        # A character with no reference images can never stage as a
        # referenceEntity — fail early and clearly here instead of letting the
        # tag drift all the way to the UI attach and abort with a cryptic
        # WireFormatError ("never rode the wire", exit 7).
        if matched_entry.kind == "entity" and not matched_entry.has_reference_images:
            log.info("mention_unresolved", name=matched_entry.name)
            raise ConfigurationError(
                detail=(
                    f"@{matched_entry.name} has no reference images — a character "
                    "needs at least one reference image before it can be tagged."
                ),
                remediation_hint=(
                    "Generate the character's reference image first, e.g. "
                    "`gflow character create --name <name> --face-prompt ...`, "
                    "then re-run."
                ),
            )

        settings = get_settings()
        is_redacted = settings.history_prompts == "redacted"
        resolved_name = matched_entry.name
        if is_redacted:
            resolved_name = hashlib.sha256(resolved_name.encode("utf-8")).hexdigest()

        log.info(
            "mention_resolved",
            name=resolved_name,
            kind=matched_entry.kind,
            id=matched_entry.id,
            shadowed=shadowed_id,
        )

        if matched_entry.id not in seen_mention_ids:
            seen_mention_ids.add(matched_entry.id)
            if matched_entry.id not in existing_set:
                resolved_list.append(
                    ResolvedMention(
                        name=resolved_name,
                        kind=matched_entry.kind,
                        id=matched_entry.id,
                        shadowed=shadowed_id,
                    )
                )

        matched_len = len(matched_entry.name)
        replacements.append((tok.start_idx, tok.start_idx + 1 + matched_len, matched_entry.name))

    total_refs = len(existing_set | seen_mention_ids)
    if total_refs > cap:
        raise ConfigurationError(
            detail=f"reference cap of {cap} exceeded for model '{model}' (requested {total_refs})"
        )

    replacements.sort(key=lambda r: r[0], reverse=True)
    current_prompt = prompt
    for start, end, name in replacements:
        current_prompt = current_prompt[:start] + name + current_prompt[end:]
    current_prompt = current_prompt.replace("@@", "@")

    return ResolvedMentions(mentions=resolved_list, de_tagged_prompt=current_prompt)


_ReqT = TypeVar("_ReqT")


def _model_str(model: object) -> str:
    """Best-effort model identifier for the reference-cap lookup.

    Enums expose ``.value``; ``None`` (a video request with no model yet) maps
    to the empty string, matching the historical inline expressions.
    """
    if model is None:
        return ""
    value = cast("Any", model)
    return str(value.value) if hasattr(model, "value") else str(model)


async def resolve_and_apply(
    client: FlowApiClient,
    req: _ReqT,
    *,
    path: Literal["image", "video"],
    project_id: str | None,
    tool_specs: tuple[str, ...],
    quiet: bool = False,
) -> _ReqT:
    """Resolve ``@``-mentions in ``req.prompt`` and expand ``--tool`` specs.

    Shared by the t2i / i2i / video CLI paths and the worker daemon (both image
    and video), which each previously inlined this block. Mentions parse →
    require an explicit project → resolve against the project's ``AssetIndex`` →
    append entity ids (both paths) and media refs (image path only) → de-tag the
    prompt. ``tool_specs``, when present, rewrite the prompt afterwards. Returns
    an updated request of the same type (or ``req`` unchanged when there is
    nothing to do).
    """
    r: Any = req

    tokens = parse_mentions(r.prompt)
    if tokens:
        if not project_id:
            raise ConfigurationError(
                detail="Mentions require an explicit --project <id>.",
                remediation_hint="Pass --project <id> to specify the project scope.",
            )

        is_image = path == "image"
        index = await AssetIndex.build_for_project(client, project_id)

        existing_refs = list(r.reference_entities)
        current_refs: list[Any] = list(r.refs) if is_image else []
        if is_image:
            existing_refs += [ref.name for ref in current_refs]

        resolved = resolve_mentions(
            tokens,
            index,
            path=path,
            model=_model_str(r.model),
            prompt=r.prompt,
            existing_refs=existing_refs,
        )

        new_entities = list(r.reference_entities)
        new_entity_names = list(r.reference_entity_names)
        for m in resolved.mentions:
            if m.kind == "entity":
                new_entities.append(m.id)
                new_entity_names.append(m.name)
            elif m.kind == "media" and is_image:
                from gflow_cli.api.image import ImageRef

                current_refs.append(ImageRef(name=m.id, display_name=m.name))

        changes: dict[str, Any] = {
            "prompt": resolved.de_tagged_prompt,
            "reference_entities": tuple(new_entities),
            "reference_entity_names": tuple(new_entity_names),
        }
        if is_image:
            changes["refs"] = tuple(current_refs)
        r = dataclasses.replace(r, **changes)

    if tool_specs:
        from gflow_cli._cli_helpers import apply_tool_option

        prompt_to_send, original_prompt, applied_tool = apply_tool_option(
            r.prompt,
            tool_specs,
            category=path,
            quiet=quiet,
        )
        r = dataclasses.replace(
            r,
            prompt=prompt_to_send,
            original_prompt=original_prompt,
            tool=applied_tool,
        )

    return cast("_ReqT", r)
