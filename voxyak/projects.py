"""Named project profiles and export targets."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import PipelineDefinition, ProcessorSpec, VoxYakConfig
from .metadata import PipelineMetadata, merge_metadata
from .modules.processors.artifact_export import (
    ArtifactExportConfig,
    ArtifactExportProcessor,
)
from .pipeline import PipelineRunner
from .sdk import OutputArtifact, RunContext, TranscriptArtifact, artifact_from_dict


_IDENTIFIER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


class ProjectDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    pipeline: str
    recording_type: str | None = Field(default=None, alias="type")
    additional_context: str | None = None
    export_targets: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_export_targets(self) -> "ProjectDefinition":
        if len(self.export_targets) != len(set(self.export_targets)):
            raise ValueError("Project export_targets must be unique.")
        return self


class ProjectsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    targets: dict[str, ArtifactExportConfig] = Field(default_factory=dict)
    projects: dict[str, ProjectDefinition]

    @model_validator(mode="after")
    def valid_references(self) -> "ProjectsFile":
        for kind, names in (
            ("project", self.projects),
            ("target", self.targets),
        ):
            invalid = next((name for name in names if not _IDENTIFIER.fullmatch(name)), None)
            if invalid:
                raise ValueError(
                    f"Invalid {kind} name {invalid!r}; use letters, numbers, "
                    "hyphens, and underscores."
                )
        for project_name, project in self.projects.items():
            unknown = set(project.export_targets) - self.targets.keys()
            if unknown:
                raise ValueError(
                    f"Project {project_name!r} references unknown export target "
                    f"{sorted(unknown)[0]!r}."
                )
        return self


def default_projects_path() -> Path:
    return Path(os.getenv("VOXYAK_PROJECTS_CONFIG", "projects.yaml"))


def load_projects(path: Path) -> ProjectsFile:
    if not path.is_file():
        raise ValueError(
            f"Project configuration not found: {path}. "
            "Copy projects.example.yaml and customize it."
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"Project configuration root must be an object: {path}")
    parsed = ProjectsFile.model_validate(raw)
    base = path.parent.resolve()
    targets = {
        name: target.model_copy(
            update={
                "destination": (
                    target.destination.expanduser()
                    if target.destination.expanduser().is_absolute()
                    else base / target.destination.expanduser()
                ).resolve()
            }
        )
        for name, target in parsed.targets.items()
    }
    return parsed.model_copy(update={"targets": targets})


def build_project_pipeline(
    project_name: str,
    projects: ProjectsFile,
    pipelines: VoxYakConfig,
) -> tuple[str, PipelineDefinition]:
    try:
        project = projects.projects[project_name]
    except KeyError as exc:
        available = ", ".join(sorted(projects.projects)) or "none"
        raise ValueError(
            f"Unknown project {project_name!r}. Available: {available}."
        ) from exc
    try:
        pipeline = pipelines.pipelines[project.pipeline].model_copy(deep=True)
    except KeyError as exc:
        available = ", ".join(sorted(pipelines.pipelines)) or "none"
        raise ValueError(
            f"Project {project_name!r} references unknown pipeline "
            f"{project.pipeline!r}. Available: {available}."
        ) from exc

    project_metadata = PipelineMetadata(
        project=project_name,
        recording_type=project.recording_type,
        project_context=project.additional_context,
    )
    pipeline.metadata = merge_metadata(pipeline.metadata, project_metadata)
    if project.export_targets:
        summary_processors = [
            processor
            for processor in pipeline.processing
            if processor.uses == "openai-summary"
        ]
        if len(summary_processors) != 1:
            raise ValueError(
                f"Project {project_name!r} uses automatic exports, so pipeline "
                f"{project.pipeline!r} must contain exactly one openai-summary "
                "processor."
            )
    existing_ids = {processor.id for processor in pipeline.processing}
    for target_name in project.export_targets:
        processor_id = f"export-{target_name}"
        if processor_id in existing_ids:
            raise ValueError(
                f"Pipeline {project.pipeline!r} already has processor id "
                f"{processor_id!r}."
            )
        pipeline.processing.append(
            ProcessorSpec(
                id=processor_id,
                uses="artifact-export",
                options=projects.targets[target_name].model_dump(mode="json"),
            )
        )
        existing_ids.add(processor_id)
    return project.pipeline, pipeline


def export_run(
    runner: PipelineRunner,
    run_reference: str | Path,
    target_name: str,
    projects: ProjectsFile,
) -> list[OutputArtifact]:
    try:
        settings = projects.targets[target_name]
    except KeyError as exc:
        available = ", ".join(sorted(projects.targets)) or "none"
        raise ValueError(
            f"Unknown export target {target_name!r}. Available: {available}."
        ) from exc

    supplied = Path(run_reference)
    run_dir = supplied if supplied.is_dir() else runner.runs_dir / supplied
    run_dir = run_dir.resolve()
    manifest = runner._load_manifest(run_dir)
    transcript_artifacts = [
        artifact_from_dict(raw)
        for stage in manifest.stages
        if stage.kind == "transcription" and stage.status == "succeeded"
        for raw in stage.artifacts
    ]
    transcripts = [
        artifact
        for artifact in transcript_artifacts
        if isinstance(artifact, TranscriptArtifact)
    ]
    if len(transcripts) != 1:
        raise ValueError("Run must contain exactly one completed transcript.")

    prior_outputs = [
        artifact
        for stage in manifest.stages
        if stage.kind == "processor" and stage.status == "succeeded"
        for raw in stage.artifacts
        if isinstance((artifact := artifact_from_dict(raw)), OutputArtifact)
    ]
    processor = ArtifactExportProcessor()
    context = RunContext(
        run_id=manifest.run_id,
        pipeline_name=manifest.pipeline_name,
        run_dir=run_dir,
        stage_id=f"manual-export.{target_name}",
        work_dir=run_dir / "manual-exports" / target_name,
        console=runner.console,
    )
    return processor.run(
        context,
        transcripts[0],
        prior_outputs,
        settings,
    )
