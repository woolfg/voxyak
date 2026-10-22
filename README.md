# VoxYak

VoxYak is a local, modular audio pipeline: acquire audio, transcribe it, then run
one or more processors such as meeting summarization or speaker-time analysis.

## Pipeline model

Every YAML pipeline has three typed stages:

1. one input module;
2. one transcription module;
3. zero or more ordered processor modules.

VoxYak includes these modules:

| Type | Module | Purpose |
| --- | --- | --- |
| Input | `file` | Use an existing local audio file |
| Input | `podcast-index` | Search PodcastIndex and download an episode |
| Input | `call-recorder` | Record microphone and system audio on Linux |
| Transcription | `faster-whisper` | Transcribe locally, with optional diarization |
| Processor | `speaker-time` | Calculate speaking time per speaker |
| Processor | `openai-summary` | Create a structured meeting brief |
| Processor | `artifact-export` | Copy a text transcript and Markdown summary to a clean shared folder |

Installed Python packages can contribute modules through the `voxyak.inputs`,
`voxyak.transcribers`, and `voxyak.processors` entry-point groups. Plugin classes
subclass the contracts in `voxyak.sdk` and expose a Pydantic `config_model`.

## Requirements

- Python 3.10 or newer
- [uv](https://docs.astral.sh/uv/)
- `ffmpeg` and `pactl` for call recording
- PodcastIndex credentials for the podcast input
- An OpenAI API key for cloud summaries

## Installation

```bash
make install
```

Configure credentials in `.env`. The `OPENAI_API_KEY` is required when a
pipeline uses the `openai-summary` processor:

```dotenv
OPENAI_API_KEY=your-api-key
```

Do not commit `.env` or share the key. After configuring the modules you use,
validate the shipped pipelines:

```bash
make validate
```

Optional speaker diarization requires the additional dependency and access to
the gated pyannote model:

```bash
make install-diarize
```

## Usage

```bash
# Inspect configuration and installed modules
voxyak pipelines list
voxyak pipelines validate
voxyak modules

# Existing local audio
voxyak transcribe recordings/interview.mp3 --language de

# Interactive podcast selection and local transcription
voxyak run podcast

# Record until Enter or Ctrl-C, then transcribe and summarize
voxyak run call-summary

# Run a project-specific pipeline and its automatic exports
make project project=geht-e-sales

# Copy an existing run to any named export target
make copy2 target=salzburg-ag-shared run=20261020-100322-a1b2c3d4

# Continue after a failed cloud processor without recording again
voxyak resume 20260721-143000-a1b2c3d4

# Standalone transcript operations
voxyak diarize runs/<run-id>/transcription/transcript.json
make summarize transcript=runs/<run-id>/transcription/transcript.json
voxyak analyze runs/<run-id>/transcription/transcript.json
```

`make summarize` reuses the `openai-summary` prompt and model settings from the
`call-summary` pipeline. Pass `pipeline=<name>` to select another pipeline or
`output=<directory>` to change the default `<transcript directory>/summary`
output location.

## Projects and shared-folder exports

Pipelines remain the reusable definitions for recording, transcription, and
processing. A project profile selects one of those pipelines, adds stable
project/type context to its metadata, and can append one or more named export
targets after all configured processors have finished. This lets different
projects use different pipeline YAML files—and therefore different summary
prompts—without duplicating destination paths in every pipeline.

Start with the example configuration:

```bash
cp projects.example.yaml projects.yaml
voxyak projects validate
voxyak projects list
```

`projects.yaml` contains reusable destinations and project profiles:

```yaml
version: 1
targets:
  salzburg-ag-shared:
    destination: /shared/salzburg-ag/meetings
    folder_template: "{date}-{context_slug}"
projects:
  geht-e-sales:
    pipeline: call-summary
    type: sales
    additional_context: |
      The project and company name is spelled "geht-e".
      Focus on sales outcomes, owners, and next steps.
    export_targets:
      - salzburg-ag-shared
```

Run `make project project=geht-e-sales` to use the selected pipeline, supply its
additional context to downstream processors, and export automatically. For an
existing run, use `make copy2 target=salzburg-ag-shared run=<run-id>`. Set
`projects_config=<path>` for a different project file.

Each export creates one folder containing only `transcript.txt` and
`summary.md`. The default folder template is `{date}-{context_slug}`; the other
available fields are `{project_slug}`, `{type_slug}`, and `{run_id}`. Existing
folders are never overwritten. Relative destination paths are resolved from the
directory containing `projects.yaml`.

After a call recording stops, VoxYak asks for optional free-text meeting
metadata: participants, number of speakers, and a one-line context. Press Enter
to skip any answer. The values are saved in the transcript source metadata,
shown in the text transcript, and supplied to the summary processor as context.

Podcast downloads are tagged with the episode title, podcast title, author,
publication date, description, and `Podcast` genre. PodcastIndex episode metadata
is also preserved in the transcript source metadata and supplied as context when
the transcript is summarized.

All stages exchange the same versioned metadata envelope. Its common fields are
`title`, `description`, `context`, `project`, `recording_type`,
`project_context`, `participants`, `participant_count`, `creator`,
`collection_title`, `language`, `published_at`, `source_url`, and `media_url`.
Modules can keep source-specific values in `attributes`; downstream stages
receive a writable copy of the same metadata object.

The summary processor sends transcript text—not audio—to the OpenAI Responses
API with API storage disabled. Selecting a pipeline containing this processor is
explicit authorization to send that transcript text to the configured service.

## Configuration

Each YAML file in `pipelines/` defines one pipeline. Its filename (without the
extension) is the pipeline name; for example, `pipelines/meeting.yaml` defines
the `meeting` pipeline. Module options are validated before a run starts.
Credentials stay in environment variables and are never written to saved
pipeline snapshots or manifests.

```yaml
version: 1
input:
  uses: file
  with:
    path: ./meeting.mp3
transcription:
  uses: faster-whisper
  with:
    model: small
    language: auto
processing:
  - id: summary
    uses: openai-summary
    with:
      model: gpt-5.6-terra
      reasoning_effort: low
      prompt: |
        Summarize the main conclusions, decisions, action items, and open questions.
```

Generated inputs, transcripts, processor output, a secret-redacted pipeline
snapshot, and an atomic status manifest are stored under `runs/<run-id>/`.
Completed stages are reused by `voxyak resume`.

## Transcript format

VoxYak writes `transcription/transcript.json` with `schema_version`, generic
source metadata, transcription metadata, timestamped segments, and full text.
The adjacent text file is a human-readable rendering of the same document.

## Development

```bash
make lint
make test
```
