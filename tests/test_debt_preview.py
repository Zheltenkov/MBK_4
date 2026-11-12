"""Exercise preview isolation in a child process without patching pytest's network."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_preview_uses_memory_routes_and_rejects_external_network(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    (tmp_path / "widget.css").write_text("body { color: black; }", encoding="utf-8")
    script = tmp_path / "preview_check.py"
    script.write_text(textwrap.dedent("""
        import json
        import os
        from pathlib import Path
        import socket
        import sys
        os.environ['MBK_DATABASE_URL'] = 'postgresql://synthetic.invalid/test'
        os.environ['OPENROUTER_API_KEY'] = 'inherited-synthetic-unused'
        from site_widget.stand.debt_preview import create_preview_app
        app = create_preview_app(Path(sys.argv[1]))
        import main
        from fastapi.testclient import TestClient
        assert 'MBK_DATABASE_URL' not in os.environ
        assert 'OPENROUTER_API_KEY' not in os.environ
        assert os.environ['MBK_CRM_LEAD_INTAKE_ENABLED'] == '0'
        assert main.crm_dialog_workflow is None
        assert main.current_context_service is None
        for operation in (
            lambda: socket.create_connection(('203.0.113.1', 9), timeout=0.2),
            lambda: socket.getaddrinfo('blocked.example', 443),
        ):
            try:
                operation()
            except OSError:
                pass
            else:
                raise AssertionError('External network guard did not reject the call')
        client = TestClient(app)
        page = client.get('/debt/')
        assert page.status_code == 200
        assert "connect-src 'self'" in page.headers['content-security-policy']
        assert client.get('/debt-assets/widget.js').status_code == 200
        widget = client.get('/widget/')
        assert 'языковая модель отключена' in widget.text
        assert main.load_config().policy_voice_enabled is False
        assert client.get('/debt-assets/../../.env').status_code == 404
        created = client.post('/chat-api/session', json={'requested_scenario':'debt'})
        assert created.status_code == 200
        sid = created.json()['session_id']
        assert created.json()['state']['plan']['fields'] == ['debts.total']
        answer = client.get('/chat-api/session/' + sid + '/stream', params={'message':'400000'})
        assert 'event: error' not in answer.text
        state = client.get('/chat-api/session/' + sid).json()
        assert state['state']['plan']['fields'] == ['client.region_class']
        assert state['state']['crm_lead']['status'] == 'not_sent'
        assert state['turn_in_progress'] is False
        client.close()
        main.store.executor.shutdown(wait=True)
        print(json.dumps({'isolation':'passed','api':'passed','static_allowlist':'passed'}))
    """), encoding="utf-8")
    environment = dict(os.environ, PYTHONPATH=str(root))
    result = subprocess.run([sys.executable, str(script), str(tmp_path)], cwd=root,
                            env=environment, capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stderr[-3000:]
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {
        "isolation": "passed", "api": "passed", "static_allowlist": "passed",
    }
