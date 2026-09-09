import json
import pytest
from backend.app.services import llm_client


@pytest.fixture(autouse=True)
def cache_in_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_client, "CACHE_DIR", str(tmp_path / "cache"))
    yield


def test_a_response_is_cached_and_the_network_is_not_hit_twice(monkeypatch):
    calls = []

    def fake(prompt, schema, model):
        calls.append(model)
        return {"ok": True}

    monkeypatch.setattr(llm_client, "_raw_call", fake)
    a = llm_client.generate_json("p", {"type": "object"}, "m", "v1", "s1")
    b = llm_client.generate_json("p", {"type": "object"}, "m", "v1", "s1")
    assert a == b == {"ok": True}
    assert len(calls) == 1


def test_a_prompt_version_change_busts_the_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(llm_client, "_raw_call",
                        lambda prompt, schema, model: calls.append(1) or {"n": len(calls)})
    llm_client.generate_json("p", {"type": "object"}, "m", "v1", "s1")
    llm_client.generate_json("p", {"type": "object"}, "m", "v2", "s1")
    assert len(calls) == 2


def test_a_schema_version_change_busts_the_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(llm_client, "_raw_call",
                        lambda prompt, schema, model: calls.append(1) or {"n": len(calls)})
    llm_client.generate_json("p", {"type": "object"}, "m", "v1", "s1")
    llm_client.generate_json("p", {"type": "object"}, "m", "v1", "s2")
    assert len(calls) == 2


def test_a_transport_error_raises_LLMUnavailable(monkeypatch):
    def boom(prompt, schema, model):
        raise RuntimeError("network down")

    monkeypatch.setattr(llm_client, "_raw_call", boom)
    with pytest.raises(llm_client.LLMUnavailable):
        llm_client.generate_json("p", {"type": "object"}, "m", "v1", "s1")


def test_unparseable_output_raises_LLMUnavailable(monkeypatch):
    monkeypatch.setattr(llm_client, "_raw_call",
                        lambda prompt, schema, model: (_ for _ in ()).throw(ValueError("not json")))
    with pytest.raises(llm_client.LLMUnavailable):
        llm_client.generate_json("p", {"type": "object"}, "m", "v1", "s1")


def test_a_failure_is_not_cached(monkeypatch, tmp_path):
    state = {"fail": True}

    def flaky(prompt, schema, model):
        if state["fail"]:
            raise RuntimeError("down")
        return {"ok": True}

    monkeypatch.setattr(llm_client, "_raw_call", flaky)
    with pytest.raises(llm_client.LLMUnavailable):
        llm_client.generate_json("p", {"type": "object"}, "m", "v1", "s1")
    state["fail"] = False
    assert llm_client.generate_json("p", {"type": "object"}, "m", "v1", "s1") == {"ok": True}
