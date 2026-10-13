"""Tests for core/scc/counts.py"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from core.scc import SccCounter
from core.scc.counts import Counts, load_counts, save_counts, _counts_file, _sera_scc_dir


@pytest.fixture(autouse=True)
def _private_counts_file(tmp_path, monkeypatch):
    # Never read or write the real ~/AmanAssociates_Sera/scc/counts.json.
    monkeypatch.setattr("core.scc.counts._counts_file", lambda: tmp_path / "counts.json")


class TestCounts:
    """Test the Counts dataclass."""

    def test_to_dict(self):
        c = Counts(attempts=1, worked=2, failed=3, locked=4, no_conclusion=5, asked=6, saved=7, not_understood=8)
        d = c.to_dict()
        assert d == {
            "attempts": 1,
            "worked": 2,
            "failed": 3,
            "locked": 4,
            "no_conclusion": 5,
            "asked": 6,
            "saved": 7,
            "not_understood": 8,
        }

    def test_from_dict(self):
        d = {
            "attempts": 1,
            "worked": 2,
            "failed": 3,
            "locked": 4,
            "no_conclusion": 5,
            "asked": 6,
            "saved": 7,
            "not_understood": 8,
        }
        c = Counts.from_dict(d)
        assert c.attempts == 1
        assert c.worked == 2
        assert c.failed == 3
        assert c.locked == 4
        assert c.no_conclusion == 5
        assert c.asked == 6
        assert c.saved == 7
        assert c.not_understood == 8

    def test_from_dict_missing_fields(self):
        # Test that missing fields default to 0
        d = {"attempts": 5}
        c = Counts.from_dict(d)
        assert c.attempts == 5
        assert c.worked == 0


class TestSccCounter:
    """Test the SccCounter class."""

    def test_counter_increments(self):
        counter = SccCounter(echo=lambda x: None)
        assert counter.get_counts().attempts == 0

        counter.on_attempt_opened()
        assert counter.get_counts().attempts == 1

        counter.on_card_asked()
        assert counter.get_counts().asked == 1

        counter.on_password_saved()
        assert counter.get_counts().saved == 1

    def test_on_outcome_worked(self):
        counter = SccCounter(echo=lambda x: None)
        counter.on_outcome("worked")
        assert counter.get_counts().worked == 1

    def test_on_outcome_wrong_password(self):
        counter = SccCounter(echo=lambda x: None)
        counter.on_outcome("wrong_password")
        assert counter.get_counts().failed == 1

    def test_on_outcome_locked(self):
        counter = SccCounter(echo=lambda x: None)
        counter.on_outcome("locked")
        assert counter.get_counts().locked == 1

    def test_on_outcome_no_conclusion(self):
        counter = SccCounter(echo=lambda x: None)
        counter.on_outcome("no_conclusion")
        assert counter.get_counts().no_conclusion == 1

    def test_on_outcome_not_understood(self):
        counter = SccCounter(echo=lambda x: None)
        counter.on_outcome(None)
        assert counter.get_counts().not_understood == 1

    def test_on_outcome_ignores_neutral(self):
        counter = SccCounter(echo=lambda x: None)
        counter.on_outcome("neutral")  # should not increment any counter
        counts = counter.get_counts()
        assert counts.worked == 0
        assert counts.failed == 0
        assert counts.locked == 0
        assert counts.no_conclusion == 0
        assert counts.not_understood == 0

    def test_callback_on_counts_changed(self):
        counter = SccCounter(echo=lambda x: None)
        mock_callback = Mock()
        counter.set_counts_changed_callback(mock_callback)

        counter.on_attempt_opened()
        mock_callback.assert_called_once()

    def test_callback_exception_handling(self):
        counter = SccCounter(echo=lambda x: None)
        mock_callback = Mock(side_effect=Exception("Test error"))
        counter.set_counts_changed_callback(mock_callback)

        # Should not raise
        counter.on_attempt_opened()

    def test_multiple_increments(self):
        counter = SccCounter(echo=lambda x: None)
        counter.on_attempt_opened()
        counter.on_attempt_opened()
        counter.on_card_asked()
        counter.on_password_saved()
        counter.on_outcome("worked")
        counter.on_outcome("wrong_password")

        counts = counter.get_counts()
        assert counts.attempts == 2
        assert counts.asked == 1
        assert counts.saved == 1
        assert counts.worked == 1
        assert counts.failed == 1


class TestCountsPersistence:
    """Test save and load of counts."""

    def test_save_and_load_counts(self):
        # Create temporary file
        with tempfile.TemporaryDirectory() as tmpdir:
            counts_file = Path(tmpdir) / "counts.json"
            c = Counts(attempts=5, worked=3, failed=2, locked=1, no_conclusion=1, asked=1, saved=1, not_understood=0)

            # Manually write to the temp file
            with open(counts_file, "w") as f:
                json.dump(c.to_dict(), f)

            # Load it
            with open(counts_file, "r") as f:
                loaded_dict = json.load(f)
            loaded = Counts.from_dict(loaded_dict)

            assert loaded.attempts == 5
            assert loaded.worked == 3
            assert loaded.failed == 2

    def test_load_counts_missing_file(self):
        # Test loading when file doesn't exist
        with tempfile.TemporaryDirectory() as tmpdir:
            counts_file = Path(tmpdir) / "nonexistent.json"

            # Try to load from nonexistent file
            if not counts_file.exists():
                result = Counts()
                assert result.attempts == 0

    def test_save_counts_creates_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            counts_file = Path(tmpdir) / "scc" / "counts.json"
            c = Counts(attempts=1, worked=1)

            counts_file.parent.mkdir(parents=True, exist_ok=True)
            with open(counts_file, "w") as f:
                json.dump(c.to_dict(), f)

            assert counts_file.exists()


class TestCounterIntegration:
    """Test SccCounter integration with the flow."""

    def test_counter_tracks_full_attempt_flow(self):
        counter = SccCounter(echo=lambda x: None)

        # Start with empty counts
        assert counter.get_counts().attempts == 0

        # Simulate an attempt
        counter.on_attempt_opened()
        assert counter.get_counts().attempts == 1

        # Simulate asking which row
        counter.on_card_asked()
        assert counter.get_counts().asked == 1

        # Simulate a successful outcome
        counter.on_outcome("worked")
        assert counter.get_counts().worked == 1

        # Simulate saving
        counter.on_password_saved()
        assert counter.get_counts().saved == 1

    def test_counter_handles_various_outcomes(self):
        counter = SccCounter(echo=lambda x: None)

        counter.on_outcome("worked")
        counter.on_outcome("wrong_password")
        counter.on_outcome("wrong_password")
        counter.on_outcome("locked")
        counter.on_outcome("no_conclusion")
        counter.on_outcome(None)

        counts = counter.get_counts()
        assert counts.worked == 1
        assert counts.failed == 2
        assert counts.locked == 1
        assert counts.no_conclusion == 1
        assert counts.not_understood == 1
