import pytest


FAKE_LOGS = [
    "2026-09-21T10:00:01Z ERROR db connection timeout",
    "2026-09-21T10:00:03Z WARN retry 3/5",
]

@pytest.fixture
def agent(monkeypatch):
    # patch where the node *looks the function up*, not where it's defined
    monkeypatch.setattr("backend.app.agents.nodes.logs.fetch_logs", lambda service: FAKE_LOGS)
    return build_workflow()

def test_logs_flow(agent):
    result = agent.invoke({"trigger": "check logs for checkout", "service": "checkout"})

    assert "logs" in result, "logs node never ran: check the routing"
    assert result["logs"] == FAKE_LOGS
