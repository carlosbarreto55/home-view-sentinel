import pytest

from sentinel.control import client
from sentinel.runtime import __main__ as runtime
from test_runtime_status import control_state


@pytest.fixture
def agent(monkeypatch):
    """A control agent that answers each request with ``answer(request)``."""
    sent = []
    answers = {}
    monkeypatch.setattr(runtime, "installed", lambda: True)
    monkeypatch.setattr(runtime.control, "send", lambda directory, request: sent.append(request))
    monkeypatch.setattr(
        runtime.control, "wait_for", lambda directory, request_id: answers["answer"](sent[-1])
    )
    return sent, answers


def test_watch_sends_a_timed_request_and_prints_the_state(agent, capsys):
    sent, answers = agent
    answers["answer"] = lambda request: control_state(applied_request=request.id)
    assert runtime.main(["watch", "--hours", "2"]) == 0
    assert (sent[0].mode, sent[0].hours) == ("timed", 2)
    assert "watching until" in capsys.readouterr().out


def test_up_asks_for_24_7(agent):
    sent, answers = agent
    answers["answer"] = lambda request: control_state(
        mode="always", until=None, applied_request=request.id
    )
    assert runtime.up() == 0
    assert (sent[0].mode, sent[0].hours) == ("always", None)


def test_a_refused_request(agent, capsys):
    sent, answers = agent
    answers["answer"] = lambda request: control_state(
        refused_request=request.id, detail="Duração de 3 h não oferecida"
    )
    assert runtime.watch(4) == 1
    assert "Refused: Duração de 3 h não oferecida" in capsys.readouterr().out


def test_no_answer_from_the_agent(agent, capsys):
    _, answers = agent
    answers["answer"] = lambda request: None
    assert runtime.down() == 1
    assert "did not answer in 15 s" in capsys.readouterr().out


def test_not_installed(monkeypatch, capsys):
    monkeypatch.setattr(runtime, "installed", lambda: False)
    assert runtime.up() == 1
    assert "make install" in capsys.readouterr().out


def test_only_offered_durations_are_accepted():
    with pytest.raises(SystemExit):
        runtime.main(["watch", "--hours", "3"])
    assert client.ANSWER_TIMEOUT == 15
