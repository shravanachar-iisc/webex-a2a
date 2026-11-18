import json
import logging

import pytest
from starlette.testclient import TestClient

from agent import ANSWER, CLARIFICATION, create_app


KEY = "test-key-not-a-secret"
HEADERS = {"Authorization": f"Bearer {KEY}", "A2A-Version": "1.0"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("A2A_POC_API_KEY", KEY)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    with TestClient(create_app()) as test_client:
        yield test_client


def request(text="hello", method="SendMessage", task=None):
    message = {
        "messageId": "message-1" if task is None else "message-2",
        "role": "ROLE_USER",
        "parts": [{"text": text}],
    }
    if task:
        message.update(taskId=task["id"], contextId=task["contextId"])
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": {"message": message},
    }


def events(response):
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


@pytest.mark.parametrize("key", ["", " ", "bad key", "non-ascii-\u00e9"])
def test_invalid_or_missing_key_fails_startup(monkeypatch, key):
    monkeypatch.setenv("A2A_POC_API_KEY", key)
    with pytest.raises((RuntimeError, ValueError)):
        create_app()


def test_discovery_and_health_are_public(client):
    assert client.get("/health").json() == {"status": "ok"}
    response = client.get("/.well-known/agent-card.json")
    assert response.status_code == 200
    card = response.json()
    assert card["supportedInterfaces"] == [
        {
            "url": "http://localhost:8000/a2a",
            "protocolBinding": "JSONRPC",
            "protocolVersion": "1.0",
        }
    ]
    assert card["capabilities"]["streaming"] is True
    assert card["securitySchemes"]["api_key"]["apiKeySecurityScheme"]["name"] == "Authorization"
    assert card["securitySchemes"]["api_key"]["apiKeySecurityScheme"]["location"] == "header"
    assert "api_key" in card["securityRequirements"][0]["schemes"]


def test_external_url_and_port(monkeypatch):
    monkeypatch.setenv("A2A_POC_API_KEY", KEY)
    monkeypatch.setenv("PORT", "9876")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://example.onrender.com/")
    with TestClient(create_app()) as client:
        card = client.get("/.well-known/agent-card.json").json()
        assert card["supportedInterfaces"][0]["url"] == "https://example.onrender.com/a2a"
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://example.onrender.com/path")
    with pytest.raises(ValueError):
        create_app()
    monkeypatch.delenv("RENDER_EXTERNAL_URL")
    with TestClient(create_app()) as client:
        assert client.get("/.well-known/agent-card.json").json()["supportedInterfaces"][0][
            "url"
        ] == "http://localhost:9876/a2a"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong"},
        {"Authorization": KEY},
        {"Authorization": f"Basic {KEY}"},
        [("Authorization", f"Bearer {KEY}"), ("Authorization", f"Bearer {KEY}")],
    ],
)
def test_authentication_rejects_bad_credentials(client, headers):
    response = client.post("/a2a", json=request(), headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_blocking_message_returns_fixed_artifact(client):
    response = client.post("/a2a", json=request("arbitrary text"), headers=HEADERS)
    assert response.status_code == 200
    task = response.json()["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["artifacts"][0]["parts"] == [{"text": ANSWER}]


def test_streaming_returns_task_status_and_artifact_events(client):
    response = client.post(
        "/a2a", json=request(method="SendStreamingMessage"), headers=HEADERS
    )
    assert response.headers["content-type"].startswith("text/event-stream")
    results = [event["result"] for event in events(response)]
    assert results[0]["task"]["status"]["state"] == "TASK_STATE_SUBMITTED"
    assert results[1]["statusUpdate"]["status"]["state"] == "TASK_STATE_WORKING"
    assert results[2]["artifactUpdate"]["artifact"]["parts"] == [{"text": ANSWER}]
    assert results[3]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    task_id = results[0]["task"]["id"]
    assert all(
        event.get("statusUpdate", event.get("artifactUpdate", {})).get("taskId") == task_id
        for event in results[1:]
    )


@pytest.mark.parametrize("method", ["SendMessage", "SendStreamingMessage"])
def test_clarify_and_follow_up_on_same_task(client, method):
    response = client.post("/a2a", json=request("clarify", method), headers=HEADERS)
    if method == "SendMessage":
        task = response.json()["result"]["task"]
        status = task["status"]
    else:
        results = [event["result"] for event in events(response)]
        task = results[0]["task"]
        status = results[-1]["statusUpdate"]["status"]
    assert status["state"] == "TASK_STATE_INPUT_REQUIRED"
    assert status["message"]["parts"] == [{"text": CLARIFICATION}]

    follow_up = client.post(
        "/a2a", json=request("additional information", task=task), headers=HEADERS
    ).json()["result"]["task"]
    assert follow_up["id"] == task["id"]
    assert follow_up["contextId"] == task["contextId"]
    assert follow_up["status"]["state"] == "TASK_STATE_COMPLETED"
    assert follow_up["artifacts"][0]["parts"] == [{"text": ANSWER}]


def test_tasks_are_not_preserved_after_restart(client, monkeypatch):
    task = client.post("/a2a", json=request("clarify"), headers=HEADERS).json()["result"]["task"]
    with TestClient(create_app()) as restarted:
        response = restarted.post(
            "/a2a",
            json={"jsonrpc": "2.0", "id": 2, "method": "GetTask", "params": {"id": task["id"]}},
            headers=HEADERS,
        )
        assert "error" in response.json()


def test_sdk_does_not_log_credentials_or_message_bodies(client, caplog):
    caplog.set_level(logging.DEBUG)
    body = "SYNTHETIC-SENSITIVE-BODY-MARKER"
    client.post("/a2a", json=request(body), headers=HEADERS)
    client.post(
        "/a2a", json=request(body, method="SendStreamingMessage"), headers=HEADERS
    )
    invalid = request(body)
    invalid["params"]["message"]["role"] = body
    client.post("/a2a", json=invalid, headers=HEADERS)
    assert body not in caplog.text
    assert KEY not in caplog.text
