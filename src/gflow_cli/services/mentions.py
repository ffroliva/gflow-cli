from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import structlog

from gflow_cli.config import get_settings
from gflow_cli.errors import ConfigurationError

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


class AssetIndex:
    def __init__(
        self, entities: list[Any] | None = None, media_assets: list[Any] | None = None
    ) -> None:
        self.entries: list[IndexEntry] = []

        for e in entities or []:
            if hasattr(e, "entity_id") and hasattr(e, "display_name"):
                self.entries.append(
                    IndexEntry(id=str(e.entity_id), name=str(e.display_name), kind="entity")
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
        try:
            entities = await client.list_characters(project_id)
        except Exception:
            entities = []

        settings = get_settings()
        db_path = settings.resolved_db_path()
        try:
            from gflow_cli.data.queries import list_project_media_assets

            media_assets = list_project_media_assets(db_path=db_path, project_id=project_id)
        except Exception:
            media_assets = []

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


def parse_mentions(text: str) -> list[MentionToken]:
    tokens: list[MentionToken] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "@":
            # Check escape @@
            if i + 1 < n and text[i + 1] == "@":
                i += 2
                continue

            # Check word boundary
            if i > 0 and re.match(r"\w", text[i - 1], re.UNICODE):
                i += 1
                continue

            start_idx = i
            j = i + 1
            while j < n:
                if text[j] in (".", "!", "?", ",", ";", ":", "\n"):
                    break
                if text[j] == "@":
                    is_escape = j + 1 < n and text[j + 1] == "@"
                    is_preceded_by_word = j > 0 and re.match(r"\w", text[j - 1], re.UNICODE)
                    if not is_escape and not is_preceded_by_word:
                        break
                j += 1

            candidate_text = text[start_idx + 1 : j]
            tokens.append(
                MentionToken(start_idx=start_idx, end_idx=j, candidate_text=candidate_text)
            )
            i = j
        else:
            i += 1
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

            available_names = sorted(list({strip_ansi(ent.name) for ent in index.entries}))
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
