"""Tests for config queue models — enums, Pydantic model, and backoff logic."""

from jenn_mesh.models.config_queue import (
    BACKOFF_MULTIPLIER,
    DEFAULT_MAX_RETRIES,
    INITIAL_RETRY_DELAY_SECONDS,
    MAX_RETRY_DELAY_SECONDS,
    RETRY_LOOP_INTERVAL_SECONDS,
    ConfigQueueEntry,
    ConfigQueueStatus,
    compute_next_retry_delay,
)


class TestConfigQueueStatus:
    """Enum value tests."""

    def test_status_values(self) -> None:
        assert ConfigQueueStatus.PENDING == "pending"
        assert ConfigQueueStatus.RETRYING == "retrying"
        assert ConfigQueueStatus.DELIVERED == "delivered"
        assert ConfigQueueStatus.FAILED_PERMANENT == "failed_permanent"
        assert ConfigQueueStatus.CANCELLED == "cancelled"

    def test_all_statuses_are_strings(self) -> None:
        for status in ConfigQueueStatus:
            assert isinstance(status.value, str)
            # str(Enum) uses .value for str enums in Pydantic usage
            assert isinstance(status, str)


class TestConfigQueueEntry:
    """Pydantic model tests."""

    def test_defaults(self) -> None:
        entry = ConfigQueueEntry(
            target_node_id="!aaa11111",
            template_role="relay-node",
            config_hash="abc123",
            yaml_content="owner:\n  long_name: Test\n",
        )
        assert entry.id is None
        assert entry.status == ConfigQueueStatus.PENDING
        assert entry.retry_count == 0
        assert entry.max_retries == DEFAULT_MAX_RETRIES
        assert entry.last_error is None
        assert entry.source_push_id is None
        assert entry.created_at is None
        assert entry.delivered_at is None

    def test_full_entry(self) -> None:
        entry = ConfigQueueEntry(
            id=42,
            target_node_id="!bbb22222",
            template_role="relay-node",
            config_hash="def456",
            yaml_content="owner:\n  long_name: Full\n",
            status=ConfigQueueStatus.RETRYING,
            retry_count=3,
            max_retries=5,
            last_error="Connection timeout",
            source_push_id="push-001",
            created_at="2025-01-01T00:00:00",
            next_retry_at="2025-01-01T00:04:00",
            last_retry_at="2025-01-01T00:02:00",
        )
        assert entry.id == 42
        assert entry.retry_count == 3
        assert entry.max_retries == 5
        assert entry.last_error == "Connection timeout"
        assert entry.source_push_id == "push-001"

    def test_retry_count_non_negative(self) -> None:
        """retry_count must be >= 0."""
        import pytest

        with pytest.raises(Exception):
            ConfigQueueEntry(
                target_node_id="!aaa11111",
                template_role="relay-node",
                config_hash="abc",
                yaml_content="x",
                retry_count=-1,
            )

    def test_max_retries_positive(self) -> None:
        """max_retries must be >= 1."""
        import pytest

        with pytest.raises(Exception):
            ConfigQueueEntry(
                target_node_id="!aaa11111",
                template_role="relay-node",
                config_hash="abc",
                yaml_content="x",
                max_retries=0,
            )


class TestBackoffSchedule:
    """Exponential backoff computation tests."""

    def test_schedule(self) -> None:
        """Verify 1m, 2m, 4m, 8m, 16m, 32m sequence."""
        expected = [60, 120, 240, 480, 960, 1920]
        for i, exp in enumerate(expected):
            assert compute_next_retry_delay(i) == exp

    def test_cap_at_max(self) -> None:
        """Delay never exceeds MAX_RETRY_DELAY_SECONDS."""
        for retry in range(6, 20):
            delay = compute_next_retry_delay(retry)
            assert delay == MAX_RETRY_DELAY_SECONDS

    def test_constants(self) -> None:
        """Verify constant values match plan."""
        assert INITIAL_RETRY_DELAY_SECONDS == 60
        assert MAX_RETRY_DELAY_SECONDS == 1920
        assert BACKOFF_MULTIPLIER == 2
        assert DEFAULT_MAX_RETRIES == 10
        assert RETRY_LOOP_INTERVAL_SECONDS == 30
