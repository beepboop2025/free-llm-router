"""Provider registry: env-key gating, tier resolution, registry invariants."""

from __future__ import annotations

from free_llm_router.providers import (
    REGISTRY,
    Provider,
    available_providers,
)


def make_provider(**overrides) -> Provider:
    base = dict(
        name="test",
        base_url="https://example.test/v1",
        api_key_env="TEST_KEY",
        models={"fast": "small", "smart": "big"},
        rpm=10,
        rpd=100,
        priority=10,
    )
    base.update(overrides)
    return Provider(**base)


def test_api_key_reads_from_environment(monkeypatch):
    p = make_provider(api_key_env="MY_PROVIDER_KEY")
    monkeypatch.delenv("MY_PROVIDER_KEY", raising=False)
    assert p.api_key is None
    monkeypatch.setenv("MY_PROVIDER_KEY", "sk-abc")
    assert p.api_key == "sk-abc"


def test_empty_string_env_var_is_treated_as_missing(monkeypatch):
    p = make_provider(api_key_env="MY_PROVIDER_KEY")
    monkeypatch.setenv("MY_PROVIDER_KEY", "")  # set-but-empty must read as None
    assert p.api_key is None


def test_model_for_resolves_known_tiers_and_none_for_unknown():
    p = make_provider(models={"fast": "small", "smart": "big"})
    assert p.model_for("fast") == "small"
    assert p.model_for("smart") == "big"
    assert p.model_for("nonexistent") is None


def test_available_providers_returns_only_keyed_entries(monkeypatch):
    # Clear every registry key, then enable exactly two.
    for p in REGISTRY:
        monkeypatch.delenv(p.api_key_env, raising=False)
    assert available_providers() == []

    monkeypatch.setenv("GROQ_API_KEY", "k1")
    monkeypatch.setenv("MISTRAL_API_KEY", "k2")
    names = {p.name for p in available_providers()}
    assert names == {"groq", "mistral"}


def test_provider_is_immutable_frozen_dataclass():
    p = make_provider()
    # frozen=True → attributes cannot be reassigned after construction.
    try:
        p.priority = 99  # type: ignore[misc]
    except Exception as exc:
        assert exc.__class__.__name__ == "FrozenInstanceError"
    else:
        raise AssertionError("Provider should be immutable (frozen dataclass)")


def test_registry_has_unique_names_and_priorities():
    names = [p.name for p in REGISTRY]
    priorities = [p.priority for p in REGISTRY]
    assert len(names) == len(set(names)), "provider names must be unique"
    assert len(priorities) == len(set(priorities)), "priorities must be a strict order"


def test_registry_every_provider_offers_both_tiers():
    for p in REGISTRY:
        assert p.model_for("fast"), f"{p.name} missing fast tier"
        assert p.model_for("smart"), f"{p.name} missing smart tier"
        assert p.rpm >= 1, f"{p.name} has non-positive rpm"
