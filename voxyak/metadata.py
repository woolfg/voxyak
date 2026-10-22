"""Shared metadata carried through every VoxYak pipeline artifact."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PipelineMetadata(BaseModel):
    """Generic, writable metadata envelope for inputs and downstream stages."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    source_module: str | None = None
    producer_module: str | None = None
    title: str | None = None
    description: str | None = None
    context: str | None = None
    project: str | None = None
    recording_type: str | None = None
    project_context: str | None = None
    participants: str | None = None
    participant_count: str | None = None
    creator: str | None = None
    collection_title: str | None = None
    language: str | None = None
    published_at: str | None = None
    source_url: str | None = None
    media_url: str | None = None
    model: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


def format_metadata(metadata: PipelineMetadata) -> str | None:
    """Render common semantic fields as context for humans and processors."""
    values = [
        ("Title", metadata.title),
        ("Description", metadata.description),
        ("Context", metadata.context),
        ("Project", metadata.project),
        ("Recording type", metadata.recording_type),
        ("Project context", metadata.project_context),
        ("Participants", metadata.participants),
        ("Participant count", metadata.participant_count),
        ("Creator", metadata.creator),
        ("Collection", metadata.collection_title),
        ("Language", metadata.language),
        ("Published", metadata.published_at),
        ("Source URL", metadata.source_url),
    ]
    lines = [f"{label}: {value}" for label, value in values if value]
    return "\n".join(lines) or None


def merge_metadata(
    base: PipelineMetadata,
    additions: PipelineMetadata,
) -> PipelineMetadata:
    """Overlay configured metadata while retaining input-specific values."""
    merged = base.model_copy(deep=True)
    values = additions.model_dump(
        exclude={"schema_version", "attributes"},
        exclude_none=True,
    )
    for field, value in values.items():
        setattr(merged, field, value)
    merged.attributes.update(additions.attributes)
    return merged
