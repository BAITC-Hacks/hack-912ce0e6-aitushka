"""No external calls: evidence isolation, budgets, versioning and API failures."""

from copy import deepcopy
import json
from pathlib import Path

from fastapi.testclient import TestClient
import httpx
import pytest

from money_graph.ai_context import prepare_context
from money_graph.ai_service import AIAnswer, AIError, AIRequest, AIService, AISettings, call_openai, settings
from money_graph.api import create_app
from money_graph.pipeline import run_analysis


def answer():
    return {"insufficient_data": False,
            "observations": [{"text": "В выборке есть входящий и исходящий поток.", "refs": ["F3"]}],
            "hypotheses": [{"text": "Совместимость потоков требует проверки.", "refs": ["F1", "F3"]}],
            "limitations": [{"text": "Начальный остаток неизвестен.", "refs": ["F1"]}],
            "next_steps": [{"text": "Запросить начальный остаток.", "refs": ["F7"]}]}


@pytest.fixture
def result(data_dir):
    return run_analysis(data_dir)


def request_for(result, **kwargs):
    return AIRequest(fingerprint=result.metadata["input_fingerprint"], **kwargs)


def service_with(provider=None, limit=50, key="test-only"):
    calls = []
    def fake(config, packet, mode, question):
        calls.append((packet, mode, question))
        return answer(), {"input_tokens": 20, "output_tokens": 10}
    service = AIService(provider=provider or fake, settings_loader=lambda: AISettings(key, max_requests=limit))
    return service, calls


def test_evidence_packet_keeps_identifiers_local_and_reconciles_daily_flows(result):
    gid = str(result.nodes.gid.iloc[1])
    context = prepare_context(result, gid)
    serialized = json.dumps(context["provider_packet"])
    assert gid not in serialized
    assert context["provider_packet"]["selected_client"].startswith("Участник_")
    for row in result.nodes.itertuples():
        assert str(row.gid) not in serialized
    flows = context["sources"][2]["data"]
    days = context["sources"][4]["data"]["days"]
    assert sum(day["in_kzt"] for day in days) == flows["in_kzt"]
    assert sum(day["out_kzt"] for day in days) == flows["out_kzt"]
    assert context["mask"](f"Что известно о {gid}?") != f"Что известно о {gid}?"
    # Numeric precision is unchanged; every restorable alias was actually sent.
    assert context["provider_packet"]["sources"][1]["data"]["priority_score"] == context["sources"][1]["data"]["priority_score"]
    packet_text = json.dumps(context["provider_packet"], ensure_ascii=False)
    assert all(alias in packet_text for alias in context["aliases"].values())


def test_event_selection_rejects_another_client_and_keeps_temporal_limits(result):
    event = result.patterns[0]
    context = prepare_context(result, event["focus_gid"], event["pattern_id"])
    assert context["scope"]["selected_event"]
    assert context["scope"]["included_events"] == 1
    assert context["sources"][-1]["data"]["limitations"] == event["limitations"]
    isolate = str(result.nodes.gid.iloc[-1])
    with pytest.raises(ValueError):
        prepare_context(result, isolate, event["pattern_id"])


def test_cache_does_not_charge_twice_and_isolated_by_question_and_snapshot(result):
    service, calls = service_with()
    gid = str(result.nodes.gid.iloc[1])
    req = request_for(result)
    first = service.explain(result, gid, req)
    assert not first["cached"]
    second = service.explain(result, gid, req)
    assert second["cached"] and len(calls) == 1 and service.used == 1
    second["answer"]["observations"][0]["text"] = "changed externally"
    assert service.explain(result, gid, req)["answer"] == first["answer"]
    service.explain(result, gid, request_for(result, mode="question", question="Почему?"))
    other = deepcopy(result)
    other.metadata["input_fingerprint"] = "a" * 64
    service.explain(other, gid, request_for(other))
    assert len(calls) == 3


@pytest.mark.parametrize("case", ["reference", "gid", "alias", "secret"])
def test_invalid_model_output_is_never_returned_or_cached(result, case):
    data = answer()
    if case == "reference":
        data["observations"][0]["refs"] = ["F9999"]
    else:
        data["observations"][0]["text"] = {"gid": "9007199254749999", "alias": "Участник_99999", "secret": "sk-aaaaaaaaaaaaaaaaaaaa"}[case]
    service, _ = service_with(lambda *args: (data, {}))
    with pytest.raises(AIError):
        service.explain(result, str(result.nodes.gid.iloc[1]), request_for(result))
    assert not service.cache and not service.inflight


def test_missing_key_stale_version_and_blank_question_never_call_provider(result):
    service, calls = service_with(key="")
    gid = str(result.nodes.gid.iloc[1])
    with pytest.raises(AIError):
        service.explain(result, gid, request_for(result))
    service, calls = service_with()
    with pytest.raises(AIError, match="изменились"):
        service.explain(result, gid, AIRequest(fingerprint="0" * 64))
    with pytest.raises(AIError, match="Введите"):
        service.explain(result, gid, request_for(result, mode="question", question="  "))
    with pytest.raises(AIError, match="API-ключи"):
        service.explain(result, gid, request_for(result, mode="question", question="sk-test-aaaaaaaaaaaaaaaaaaaa"))
    assert not calls and service.used == 0


def test_request_budget_and_provider_failure_release_slot(result):
    def failure(*args):
        raise AIError("Временная ошибка")
    service, _ = service_with(failure, limit=1)
    gid = str(result.nodes.gid.iloc[1])
    with pytest.raises(AIError, match="Временная"):
        service.explain(result, gid, request_for(result))
    assert not service.inflight and not service.cache
    with pytest.raises(AIError, match="лимит"):
        service.explain(result, gid, request_for(result))
    assert service.used == 1


def test_settings_only_reads_named_values_and_environment_wins(tmp_path, monkeypatch):
    for key in ("OPENAI_API_KEY", "OPENAI_MODEL", "AI_MAX_REQUESTS"):
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / "local.env"
    path.write_text('OPENAI_API_KEY="fake-private"\nOPENAI_MODEL=gpt-5.4-mini\nAI_MAX_REQUESTS=3\nUNRELATED=abc\n', encoding="utf-8")
    config = settings(path)
    assert config.api_key == "fake-private" and config.max_requests == 3
    assert "fake-private" not in repr(config)
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    assert settings(path).api_key == "environment-key"
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert settings(path).api_key == ""


@pytest.mark.parametrize("status,expected", [(401, 503), (403, 503), (429, 429), (500, 502)])
def test_provider_errors_do_not_echo_response_or_credentials(monkeypatch, status, expected):
    class FakeClient:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kwargs):
            return httpx.Response(status, json={"error": "private-key-must-not-escape"})
    monkeypatch.setattr("money_graph.ai_service.httpx.Client", FakeClient)
    with pytest.raises(AIError) as caught:
        call_openai(AISettings("private-key-must-not-escape"), {"sources": [{"id": "F1"}]}, "explain", "")
    assert caught.value.status == expected
    assert "private-key" not in str(caught.value)


def test_responses_payload_disables_storage_and_validates_completed_output(monkeypatch):
    captured = {}
    class FakeClient:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kwargs):
            captured.update(kwargs["json"])
            assert url == "https://api.openai.com/v1/responses"
            return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [
                {"type": "output_text", "text": json.dumps(answer())}]}]})
    monkeypatch.setattr("money_graph.ai_service.httpx.Client", FakeClient)
    parsed, _ = call_openai(AISettings("fake"), {"sources": [{"id": "F1"}]}, "explain", "")
    assert isinstance(parsed, AIAnswer)
    assert captured["store"] is False and "tools" not in captured
    assert captured["text"]["format"]["strict"] is True


def test_ai_http_contract_origin_scope_and_status(data_dir, monkeypatch):
    monkeypatch.setenv("MONEY_GRAPH_DATA", str(data_dir))
    app = create_app()
    service, calls = service_with()
    app.state.ai_service.provider = service.provider
    app.state.ai_service.settings_loader = service.settings_loader
    with TestClient(app) as client:
        assert "api_key" not in client.get("/api/ai/status").text
        fp = client.get("/api/health").json()["fingerprint"]
        gid = str(2**53 + 2)
        body = {"fingerprint": fp, "mode": "explain"}
        response = client.post(f"/api/clients/{gid}/ai", json=body)
        assert response.status_code == 200 and response.json()["gid"] == gid
        assert response.headers["cache-control"] == "no-store"
        assert client.post(f"/api/clients/{gid}/ai", json=body, headers={"Origin": "https://untrusted.example"}).status_code == 403
        assert client.post(f"/api/clients/{gid}/ai", json={**body, "fingerprint": "a" * 64}).status_code == 409
        assert client.post("/api/clients/999/ai", json=body).status_code == 404
        assert client.post(f"/api/clients/{gid}/ai", json={**body, "question": "x" * 1501}).status_code == 422
        assert len(calls) == 1


def test_ai_allows_explicit_deployment_origin(data_dir, monkeypatch):
    monkeypatch.setenv("MONEY_GRAPH_DATA", str(data_dir))
    monkeypatch.setenv("AI_ALLOWED_ORIGINS", "https://dashboard.example, https://second.example/")
    app = create_app()
    service, calls = service_with()
    app.state.ai_service.provider = service.provider
    app.state.ai_service.settings_loader = service.settings_loader
    with TestClient(app) as client:
        fp = client.get("/api/health").json()["fingerprint"]
        response = client.post(
            f"/api/clients/{2**53 + 2}/ai",
            json={"fingerprint": fp, "mode": "explain"},
            headers={"Origin": "https://dashboard.example"},
        )
    assert response.status_code == 200 and len(calls) == 1
