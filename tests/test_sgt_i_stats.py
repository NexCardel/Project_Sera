"""
Tests for core/sgt_i/stats.py (blueprint 14.4 step 3's container-kind table, 14.5 rule 3). Every
PAN, client id and value here is fictional.
"""

import json
import os

from core.sgt_i import stats


# ── the salt and the hash ─────────────────────────────────────────────────────────
def test_generate_salt_is_a_random_hex_key():
    a, b = stats.generate_salt(), stats.generate_salt()
    assert a != b
    assert len(a) == stats.SALT_BYTES * 2
    int(a, 16)          # does not raise: it is hex


def test_salted_hash_is_deterministic_and_truncated():
    salt = stats.generate_salt()
    h1 = stats.salted_hash("ABCPD1234E", salt, "value")
    h2 = stats.salted_hash("ABCPD1234E", salt, "value")
    assert h1 == h2
    assert len(h1) == stats.HASH_LEN


def test_salted_hash_differs_by_salt_and_by_domain():
    salt_a, salt_b = stats.generate_salt(), stats.generate_salt()
    assert stats.salted_hash("X", salt_a, "value") != stats.salted_hash("X", salt_b, "value")
    same_salt = salt_a
    assert stats.salted_hash("X", same_salt, "value") != stats.salted_hash("X", same_salt, "client")


# ── classify_container: the pure table ────────────────────────────────────────────
def test_identical_for_every_client_is_template():
    kind = stats.classify_container(n=6, distinct=1, clients=3, stable_clients=0, changed_clients=0)
    assert kind.kind == "template"
    assert kind.confidence is not None


def test_one_stable_client_alone_is_not_yet_template_or_profile():
    # Only one client has ever been seen holding this container steady - a second client agreeing
    # would make it a template, a second client disagreeing would make it a profile datapoint.
    kind = stats.classify_container(n=5, distinct=1, clients=1, stable_clients=1, changed_clients=0)
    assert kind.kind is None


def test_a_few_repeating_values_are_a_vocabulary():
    kind = stats.classify_container(n=8, distinct=3, clients=0, stable_clients=0, changed_clients=0)
    assert kind.kind == "vocabulary"
    assert kind.confidence is None       # a counting rule, not a zero-exception bound


def test_stable_per_client_but_different_between_clients_is_a_profile():
    kind = stats.classify_container(n=9, distinct=3, clients=3, stable_clients=3, changed_clients=0)
    assert kind.kind == "profile"
    assert kind.confidence is not None


def test_changing_for_the_same_client_is_a_dataset():
    kind = stats.classify_container(n=9, distinct=5, clients=3, stable_clients=0, changed_clients=2)
    assert kind.kind == "dataset"
    assert kind.confidence is not None


def test_never_repeated_is_an_identifier():
    kind = stats.classify_container(n=6, distinct=6, clients=0, stable_clients=0, changed_clients=0)
    assert kind.kind == "identifier"
    assert kind.confidence is not None


def test_too_few_observations_never_guesses():
    kind = stats.classify_container(n=2, distinct=1, clients=2, stable_clients=0, changed_clients=0)
    assert kind.kind is None
    assert kind.n == 2 and kind.distinct == 1


# ── ContainerStats: observing and persisting ──────────────────────────────────────
def test_observe_then_classify_a_profile_container(tmp_path):
    cs = stats.ContainerStats(directory=tmp_path)
    # three clients, each stable at their own value across three sightings
    for client, value in (("client-a", "Maharashtra"), ("client-b", "Delhi"), ("client-c", "Gujarat")):
        for _ in range(3):
            cs.observe("Address > State", value, client=client)
    assert cs.classify("Address > State").kind == "profile"


def test_observe_then_classify_a_dataset_container(tmp_path):
    cs = stats.ContainerStats(directory=tmp_path)
    # "10000" recurs for client-a and, later, for client-c too - a coincidence real amounts have
    # plenty of room for - so this is not also (mis)read as an identifier (never repeats at all).
    for client, values in (("client-a", ["10000", "12000", "10000"]),
                            ("client-b", ["20000", "20500"]),
                            ("client-c", ["10000", "700", "900"])):
        for value in values:
            cs.observe("Return > Total Tax", value, client=client)
    assert cs.classify("Return > Total Tax").kind == "dataset"


def test_observe_then_classify_a_template_container(tmp_path):
    cs = stats.ContainerStats(directory=tmp_path)
    for client in ("client-a", "client-b", "client-c"):
        cs.observe("Footer > Disclaimer", "This is an auto-generated acknowledgement", client=client)
    assert cs.classify("Footer > Disclaimer").kind == "template"


def test_observe_then_classify_an_identifier_container(tmp_path):
    cs = stats.ContainerStats(directory=tmp_path)
    for ack in ("111122223333", "444455556666", "777788889999", "121212121212"):
        cs.observe("Acknowledgement Number", ack)
    assert cs.classify("Acknowledgement Number").kind == "identifier"


def test_unseen_container_classifies_to_none(tmp_path):
    cs = stats.ContainerStats(directory=tmp_path)
    assert cs.classify("Never Seen") is None


def test_stats_and_salt_persist_across_instances(tmp_path):
    first = stats.ContainerStats(directory=tmp_path)
    for client in ("client-a", "client-b"):
        first.observe("Filing Status", "Verified", client=client)
    first.save()
    salt = first.salt

    second = stats.ContainerStats(directory=tmp_path)
    assert second.salt == salt
    cell = second.data["containers"]["Filing Status"]
    assert cell["n"] == 2
    assert len(cell["hashes"]) == 1     # both clients saw the identical value


def test_tracked_values_are_capped_and_overflow_is_counted(tmp_path, monkeypatch):
    monkeypatch.setattr(stats, "MAX_TRACKED_VALUES", 3)
    cs = stats.ContainerStats(directory=tmp_path)
    for v in ("a", "b", "c", "d", "e"):
        cs.observe("Some Container", v)
    cell = cs.data["containers"]["Some Container"]
    assert len(cell["hashes"]) == 3
    assert cell["hash_overflow"] == 2
    assert cell["n"] == 5


# ── the disk-level safety net ────────────────────────────────────────────────────
def test_nothing_written_to_disk_ever_contains_a_raw_value_or_client_id(tmp_path):
    cs = stats.ContainerStats(directory=tmp_path)
    secret_value = "ABCPD1234E-secret-value"
    secret_client = "client-secret-id-9876"
    for _ in range(3):
        cs.observe("PAN", secret_value, client=secret_client)
    cs.save()

    found_any_file = False
    for root, _, names in os.walk(tmp_path):
        for name in names:
            found_any_file = True
            with open(os.path.join(root, name), "rb") as f:
                blob = f.read()
            assert secret_value.encode("utf-8") not in blob
            assert secret_client.encode("utf-8") not in blob
    assert found_any_file


def test_container_stats_file_holds_only_counts_and_hashes(tmp_path):
    cs = stats.ContainerStats(directory=tmp_path)
    cs.observe("PAN", "ABCPD1234E", client="client-a")
    cs.save()
    loaded = json.loads((tmp_path / stats.STATS_FILE).read_text(encoding="utf-8"))
    cell = loaded["containers"]["PAN"]
    assert set(cell) == {"n", "hashes", "hash_overflow", "clients", "client_overflow"}
    assert all(len(h) == stats.HASH_LEN for h in cell["hashes"])
    for entry in cell["clients"].values():
        assert set(entry) == {"hashes", "n"}
        assert all(len(h) == stats.HASH_LEN for h in entry["hashes"])
