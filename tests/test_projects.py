from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from voxyak.cli import main
from voxyak.config import (
    ModuleSpec,
    PipelineDefinition,
    ProcessorSpec,
    VoxYakConfig,
)
from voxyak.projects import build_project_pipeline, load_projects
from voxyak.pipeline import RunManifest, StageRecord
from voxyak.sdk import OutputArtifact, TranscriptArtifact
from voxyak.transcript import (
    TranscriptDocument,
    TranscriptSegment,
    TranscriptSource,
    TranscriptionDetails,
    write_transcript,
)


def _write_projects(tmp_path: Path) -> Path:
    path = tmp_path / "projects.yaml"
    path.write_text(
        """\
version: 1
targets:
  shared:
    destination: ./exports
    folder_template: "{date}-{project_slug}-{context_slug}"
projects:
  customer-sales:
    pipeline: sales-summary
    type: sales
    additional_context: Preserve the spelling geht-e.
    export_targets: [shared]
""",
        encoding="utf-8",
    )
    return path


def _pipelines() -> VoxYakConfig:
    return VoxYakConfig(
        pipelines={
            "sales-summary": PipelineDefinition(
                input=ModuleSpec(uses="call-recorder"),
                transcription=ModuleSpec(uses="faster-whisper"),
                processing=[ProcessorSpec(id="summary", uses="openai-summary")],
            )
        }
    )


def test_project_builds_on_pipeline_and_adds_context_and_exports(
    tmp_path: Path,
) -> None:
    projects = load_projects(_write_projects(tmp_path))
    pipeline_name, pipeline = build_project_pipeline(
        "customer-sales",
        projects,
        _pipelines(),
    )

    assert pipeline_name == "sales-summary"
    assert pipeline.metadata.project == "customer-sales"
    assert pipeline.metadata.recording_type == "sales"
    assert pipeline.metadata.project_context == "Preserve the spelling geht-e."
    assert [processor.id for processor in pipeline.processing] == [
        "summary",
        "export-shared",
    ]
    assert pipeline.processing[-1].uses == "artifact-export"
    assert pipeline.processing[-1].options["destination"] == str(
        (tmp_path / "exports").resolve()
    )


def test_project_config_rejects_unknown_export_target(tmp_path: Path) -> None:
    path = tmp_path / "projects.yaml"
    path.write_text(
        """\
version: 1
projects:
  example:
    pipeline: call-summary
    export_targets: [missing]
""",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="unknown export target"):
        load_projects(path)


def test_manual_export_command_copies_a_completed_run(tmp_path: Path) -> None:
    projects_path = _write_projects(tmp_path)
    run_dir = tmp_path / "runs" / "run-1"
    document = TranscriptDocument(
        source=TranscriptSource(
            module="call-recorder",
            title="call-2026-10-20-100322",
            audio_path="call.mp3",
            metadata={"context": "Customer roadmap"},
        ),
        transcription=TranscriptionDetails(
            module="faster-whisper",
            model="small",
            language="en",
            language_probability=1,
            duration_seconds=1,
            created_at="2026-10-20T10:04:00+00:00",
        ),
        segments=[TranscriptSegment(start=0, end=1, text="Roadmap")],
        full_text="Roadmap",
    )
    json_path, text_path = write_transcript(document, run_dir / "transcription")
    transcript = TranscriptArtifact(
        path=json_path.resolve(),
        text_path=text_path.resolve(),
        language="en",
        duration_seconds=1,
        metadata=document.source.metadata.model_copy(deep=True),
    )
    summary_path = run_dir / "processing" / "summary" / "summary.md"
    summary_path.parent.mkdir(parents=True)
    summary_path.write_text("# Roadmap\n", encoding="utf-8")
    summary_metadata = transcript.metadata.model_copy(deep=True)
    summary_metadata.producer_module = "openai-summary"
    summary = OutputArtifact(
        path=summary_path.resolve(),
        media_type="text/markdown",
        metadata=summary_metadata,
    )
    manifest = RunManifest(
        run_id="run-1",
        pipeline_name="call-summary",
        status="succeeded",
        created_at="2026-10-20T10:03:22+00:00",
        updated_at="2026-10-20T10:04:22+00:00",
        stages=[
            StageRecord(
                id="transcription",
                kind="transcription",
                module="faster-whisper",
                module_version="1",
                status="succeeded",
                artifacts=[transcript.model_dump(mode="json")],
            ),
            StageRecord(
                id="processing.summary",
                kind="processor",
                module="openai-summary",
                module_version="1",
                status="succeeded",
                artifacts=[summary.model_dump(mode="json")],
            ),
        ],
    )
    (run_dir / "manifest.json").write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )

    result = main(
        [
            "--runs-dir",
            str(tmp_path / "runs"),
            "--projects-config",
            str(projects_path),
            "projects",
            "export",
            "shared",
            "run-1",
        ]
    )

    assert result == 0
    exported = (
        tmp_path / "exports" / "2026-10-20-unassigned-customer-roadmap"
    )
    assert {path.name for path in exported.iterdir()} == {
        "transcript.txt",
        "summary.md",
    }


def test_example_project_configuration_parses() -> None:
    config = load_projects(Path("projects.example.yaml"))
    assert "geht-e-sales" in config.projects
    assert "salzburg-ag-shared" in config.targets
