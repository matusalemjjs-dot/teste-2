from enum import StrEnum


class ProjectStatus(StrEnum):
    pending = "pending"
    uploaded = "uploaded"
    queued = "queued"
    processing = "processing"
    completed = "completed"
    failed = "failed"
    canceled = "canceled"


class AssetKind(StrEnum):
    source_video = "source_video"
    audio = "audio"
    clip_video = "clip_video"
    thumbnail = "thumbnail"
    subtitle = "subtitle"


class AssetStatus(StrEnum):
    pending = "pending"
    ready = "ready"
    invalid = "invalid"
    deleted = "deleted"


class ClipStatus(StrEnum):
    pending = "pending"
    rendering = "rendering"
    ready = "ready"
    failed = "failed"


class ClipLayout(StrEnum):
    center_crop = "center_crop"
    blur_background = "blur_background"


class JobType(StrEnum):
    process_project = "process_project"
    render_clip = "render_clip"


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    canceled = "canceled"


class UsageKind(StrEnum):
    processed_seconds = "processed_seconds"
    transcription_seconds = "transcription_seconds"
    llm_tokens = "llm_tokens"
    render_seconds = "render_seconds"


class SubscriptionStatus(StrEnum):
    active = "active"
    suspended = "suspended"
    canceled = "canceled"


def check_in(column: str, enum_cls: type[StrEnum]) -> str:
    values = ", ".join(f"'{v.value}'" for v in enum_cls)
    return f"{column} IN ({values})"
