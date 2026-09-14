# tests/dispatcher/test_vibe_kanban_client.py
from dispatcher.vibe_kanban_client import VibeKanbanClient


def test_list_tasks_parses_call_result(monkeypatch) -> None:
    client = VibeKanbanClient("http://127.0.0.1:9100/sse")
    monkeypatch.setattr(
        client, "_call",
        lambda tool, args: [{"id": "t1", "project": "myproj", "title": "Do X", "status": "pending"}],
    )

    tasks = client.list_tasks(project="myproj")

    assert len(tasks) == 1
    assert tasks[0].id == "t1"
    assert tasks[0].status == "pending"


def test_create_task_returns_id(monkeypatch) -> None:
    client = VibeKanbanClient("http://127.0.0.1:9100/sse")
    captured = {}

    def fake_call(tool, args):
        captured["tool"] = tool
        captured["args"] = args
        return {"id": "t2"}

    monkeypatch.setattr(client, "_call", fake_call)

    task_id = client.create_task("myproj", "Do Y", "description")

    assert task_id == "t2"
    assert captured["tool"] == "create_task"
    assert captured["args"] == {"project": "myproj", "title": "Do Y", "description": "description"}


def test_update_task_status_calls_update_tool(monkeypatch) -> None:
    client = VibeKanbanClient("http://127.0.0.1:9100/sse")
    captured = {}
    monkeypatch.setattr(client, "_call", lambda tool, args: captured.update(tool=tool, args=args))

    client.update_task_status("t1", "done")

    assert captured["tool"] == "update_task"
    assert captured["args"] == {"id": "t1", "status": "done"}
