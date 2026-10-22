"""Copy a transcript and meeting summary into a clean shared folder."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from string import Formatter

from pydantic import BaseModel, Field, field_validator

from ...sdk import (
    ModuleConfig,
    OutputArtifact,
    ProcessorModule,
    RunContext,
    TranscriptArtifact,
)
from ...transcript import TranscriptDocument, load_transcript


_ALLOWED_TEMPLATE_FIELDS = {
    "context_slug",
    "date",
    "project_slug",
    "run_id",
    "type_slug",
}


class ArtifactExportConfig(ModuleConfig):
    destination: Path
    folder_template: str = Field(
        default="{date}-{context_slug}",
        min_length=1,
    )

    @field_validator("folder_template")
    @classmethod
    def valid_folder_template(cls, value: str) -> str:
        try:
            fields = {
                field_name
                for _, field_name, _, _ in Formatter().parse(value)
                if field_name
            }
        except ValueError as exc:
            raise ValueError(f"Invalid folder template: {exc}") from exc
        unknown = fields - _ALLOWED_TEMPLATE_FIELDS
        if unknown:
            supported = ", ".join(sorted(_ALLOWED_TEMPLATE_FIELDS))
            raise ValueError(
                f"Unknown folder template field {sorted(unknown)[0]!r}; "
                f"supported fields: {supported}."
            )
        return value


def _slug(value: str | None, *, fallback: str) -> str:
    normalized = re.sub(r"[^\w]+", "-", (value or "").casefold(), flags=re.UNICODE)
    normalized = normalized.strip("-_").replace("_", "-")
    return normalized[:80].rstrip("-") or fallback


def _recording_date(document: TranscriptDocument) -> str:
    candidates = (
        document.source.metadata.published_at,
        document.source.title,
        document.transcription.created_at,
    )
    for candidate in candidates:
        if candidate and (match := re.search(r"\b\d{4}-\d{2}-\d{2}\b", candidate)):
            return match.group(0)
    return "undated"


def _folder_name(
    document: TranscriptDocument,
    context: RunContext,
    settings: ArtifactExportConfig,
) -> str:
    metadata = document.source.metadata
    values = {
        "context_slug": _slug(
            metadata.context or document.source.title,
            fallback="meeting",
        ),
        "date": _recording_date(document),
        "project_slug": _slug(metadata.project, fallback="unassigned"),
        "run_id": _slug(context.run_id, fallback="run"),
        "type_slug": _slug(metadata.recording_type, fallback="recording"),
    }
    name = settings.folder_template.format_map(values).strip()
    if not name or name in {".", ".."} or Path(name).name != name:
        raise ValueError(
            "Export folder_template must render to one relative folder name."
        )
    return name


def _summary_output(prior_outputs: list[OutputArtifact]) -> OutputArtifact:
    matches = [
        output
        for output in prior_outputs
        if output.media_type == "text/markdown"
        and output.metadata.producer_module == "openai-summary"
    ]
    if len(matches) != 1:
        raise ValueError(
            "artifact-export requires exactly one Markdown output from "
            "openai-summary before it in the pipeline."
        )
    return matches[0]


class ArtifactExportProcessor(ProcessorModule):
    config_model = ArtifactExportConfig

    def run(
        self,
        context: RunContext,
        transcript: TranscriptArtifact,
        prior_outputs: list[OutputArtifact],
        config: BaseModel,
    ) -> list[OutputArtifact]:
        settings = ArtifactExportConfig.model_validate(config)
        document = load_transcript(transcript.path)
        summary = _summary_output(prior_outputs)
        destination_root = settings.destination.expanduser().resolve()
        destination_root.mkdir(parents=True, exist_ok=True)
        destination = destination_root / _folder_name(document, context, settings)
        if destination.exists():
            raise ValueError(
                f"Export destination already exists; refusing to overwrite: "
                f"{destination}"
            )

        temporary = Path(
            tempfile.mkdtemp(prefix=".voxyak-export-", dir=destination_root)
        )
        try:
            shutil.copy2(transcript.text_path, temporary / "transcript.txt")
            shutil.copy2(summary.path, temporary / "summary.md")
            temporary.rename(destination)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

        context.work_dir.mkdir(parents=True, exist_ok=True)
        receipt_path = context.work_dir / "export.json"
        receipt_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "destination": str(destination),
                    "files": ["transcript.txt", "summary.md"],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        output_metadata = transcript.metadata.model_copy(deep=True)
        output_metadata.producer_module = "artifact-export"
        return [
            OutputArtifact(
                path=receipt_path.resolve(),
                media_type="application/json",
                metadata=output_metadata,
            )
        ]
