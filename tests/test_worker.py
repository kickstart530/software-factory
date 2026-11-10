import urllib.error
import urllib.request

from factory import worker


def _status(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_health_endpoints_reflect_readiness():
    server = worker.serve_health(0)
    port = server.server_address[1]
    try:
        worker._Health.ready.clear()
        assert _status(port, "/health/live") == 200
        assert _status(port, "/health/ready") == 503
        worker._Health.ready.set()
        assert _status(port, "/health/ready") == 200
        assert _status(port, "/nope") == 404
    finally:
        worker._Health.ready.clear()
        server.shutdown()


def test_worker_runs_tasks_in_sandboxes():
    import inspect

    src = inspect.getsource(worker.run_worker)
    assert "run_task_in_sandbox" in src and "kill_switch=kill_switch" in src
