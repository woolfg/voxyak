from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from pydantic import ValidationError
from rich.console import Console

from voxyak.modules.processors.artifact_export import (
    ArtifactExportConfig,
    ArtifactExportProcessor,
)
from voxyak.sdk import OutputArtifact, RunContext, TranscriptArtifact
from voxyak.transcript import (
    TranscriptDocument,
    TranscriptSegment,
    TranscriptSource,
    TranscriptionDetails,
    write_transcript,
)


def _transcript(tmp_path: Path) -> TranscriptArtifact:
    document = TranscriptDocument(
        source=TranscriptSource(
            module="call-recorder",
            title="call-2026-10-20-100322",
            audio_path="call.mp3",
            metadata={
                "context": "geht-e sales meeting Salzburg AG",
                "project": "geht-e-sales",
                "recording_type": "sales",
            },
        ),
        transcription=TranscriptionDetails(
            module="faster-whisper",
            model="small",
            language="en",
            language_probability=1,
            duration_seconds=2,
            created_at="2026-10-20T10:04:00+00:00",
        ),
        segments=[TranscriptSegment(start=0, end=2, text="Discussed next steps.")],
        full_text="Discussed next steps.",
    )
    json_path, text_path = write_transcript(document, tmp_path / "transcription")
    return TranscriptArtifact(
        path=json_path,
        text_path=text_path,
        language="en",
        duration_seconds=2,
        metadata=document.source.metadata.model_copy(deep=True),
    )


def _summary(tmp_path: Path, transcript: TranscriptArtifact) -> OutputArtifact:
    path = tmp_path / "summary.md"
    path.write_text("# Summary\n", encoding="utf-8")
    metadata = transcript.metadata.model_copy(deep=True)
    metadata.producer_module = "openai-summary"
    return OutputArtifact(path=path, media_type="text/markdown", metadata=metadata)


def _context(tmp_path: Path) -> RunContext:
    return RunContext(
        run_id="20261020-100322-abcd1234",
        pipeline_name="geht-e-sales:call-summary",
        run_dir=tmp_path,
        stage_id="processing.export-shared",
        work_dir=tmp_path / "processing" / "export-shared",
        console=Console(file=StringIO(), force_terminal=False),
    )


def test_export_copies_only_text_transcript_and_markdown_summary(
    tmp_path: Path,
) -> None:
    transcript = _transcript(tmp_path)
    output = ArtifactExportProcessor().run(
        _context(tmp_path),
        transcript,
        [_summary(tmp_path, transcript)],
        ArtifactExportConfig(destination=tmp_path / "shared"),
    )[0]

    destination = (
        tmp_path / "shared" / "2026-10-20-geht-e-sales-meeting-salzburg-ag"
    )
    assert {path.name for path in destination.iterdir()} == {
        "transcript.txt",
        "summary.md",
    }
    assert (destination / "summary.md").read_text(encoding="utf-8") == "# Summary\n"
    receipt = json.loads(output.path.read_text(encoding="utf-8"))
    assert receipt["destination"] == str(destination)
    assert output.metadata.producer_module == "artifact-export"


def test_export_refuses_to_overwrite_an_existing_folder(tmp_path: Path) -> None:
    transcript = _transcript(tmp_path)
    summary = _summary(tmp_path, transcript)
    settings = ArtifactExportConfig(destination=tmp_path / "shared")
    processor = ArtifactExportProcessor()
    processor.run(_context(tmp_path), transcript, [summary], settings)

    with pytest.raises(ValueError, match="refusing to overwrite"):
        processor.run(_context(tmp_path), transcript, [summary], settings)


def test_export_template_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Unknown folder template field"):
        ArtifactExportConfig(
            destination=Path("shared"),
            folder_template="{customer}",
        )
