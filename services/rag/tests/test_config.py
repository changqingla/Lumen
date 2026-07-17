from __future__ import annotations

import pytest
from pydantic import ValidationError

from config import DocPipelineSettings


def test_task_lease_timing_accepts_heartbeat_inside_visibility_window():
    settings = DocPipelineSettings(
        TASK_VISIBILITY_TIMEOUT_SECONDS=30,
        TASK_HEARTBEAT_INTERVAL_SECONDS=10,
    )

    assert settings.TASK_HEARTBEAT_INTERVAL_SECONDS == 10
    assert settings.TASK_VISIBILITY_TIMEOUT_SECONDS == 30


@pytest.mark.parametrize("heartbeat", [30, 31])
def test_task_lease_timing_rejects_heartbeat_at_or_after_visibility(heartbeat):
    with pytest.raises(
        ValidationError,
        match="TASK_HEARTBEAT_INTERVAL_SECONDS must be less than",
    ):
        DocPipelineSettings(
            TASK_VISIBILITY_TIMEOUT_SECONDS=30,
            TASK_HEARTBEAT_INTERVAL_SECONDS=heartbeat,
        )


@pytest.mark.parametrize(
    "field",
    [
        "TASK_VISIBILITY_TIMEOUT_SECONDS",
        "TASK_HEARTBEAT_INTERVAL_SECONDS",
        "TASK_STALE_RECOVERY_INTERVAL_SECONDS",
    ],
)
def test_task_lease_timing_requires_positive_values(field):
    with pytest.raises(ValidationError):
        DocPipelineSettings(**{field: 0})
