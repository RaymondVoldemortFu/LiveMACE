"""Run isolated HTTP/WS smoke checks, or serve the built UI for a quick visual check."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--port', type=int, default=5644)
    args = parser.parse_args()
    with TemporaryDirectory(prefix='wave4-smoke-') as directory:
        os.environ['DATABASE_URL'] = f'sqlite:///{directory}/smoke.sqlite'
        os.environ['WAVE3_PRODUCTION'] = 'false'
        os.environ['ALPHA_ARENA_EXTENSION_DIRS'] = ''
        os.environ['ALPHA_ARENA_DISABLED_EXTENSIONS'] = ''
        os.environ['ALPHA_ARENA_ALLOWED_CAPABILITIES'] = 'market.read,account.read,memory.read,memory.write,network.read,sandbox.write,trading.write'
        from benchmark.bootstrap.app import create_app, AppSettings
        from benchmark.bootstrap.runtime import StartupMode
        from fastapi.testclient import TestClient
        from schemas.control_plane import PortfolioSnapshot

        static = Path(__file__).resolve().parents[2] / 'frontend/dist'
        app = create_app(AppSettings(static_dir=str(static)), mode=StartupMode.NO_BACKGROUND)
        if args.serve:
            import uvicorn
            uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')
            return
        with TestClient(app) as client:
            assert client.get('/api/ready').status_code == 200
            assert client.get('/').status_code == 200
            accounts = client.get('/api/account/list')
            assert accounts.status_code == 200, accounts.text
            account_id = accounts.json()[0]['id']
            paths = ['/api/extensions', '/api/extensions/tools', '/api/extensions/agents', '/api/extensions/prompts', '/api/rules/summary', '/api/rules/list', f'/api/account/{account_id}/runtime-config', f'/api/agent/history/{account_id}', f'/api/evaluation/checkpoints/account/{account_id}', '/api/evaluation/checkpoints/leaderboard', '/api/evaluation/checkpoints/compare', f'/api/compliance/account/{account_id}/history', f'/api/compliance/account/{account_id}/stats', f'/api/compliance/account/{account_id}/trend', f'/api/compliance/recent-decisions?account_id={account_id}']
            for path in paths:
                response = client.get(path)
                assert response.status_code == 200, (path, response.text)
            with client.websocket_connect('/ws') as ws:
                ws.send_json({'type':'bootstrap', 'username':'default', 'initial_capital':10000})
                assert ws.receive_json()['type'] == 'bootstrap_ok'
                snapshot = ws.receive_json()
                PortfolioSnapshot.model_validate(snapshot)
                ws.send_json({'type':'ping'})
                assert ws.receive_json()['type'] == 'pong'
                ws.send_json({'type':'get_asset_curve', 'timeframe':'1h'})
                curve = ws.receive_json()
                assert curve['type'] == 'asset_curve_data', curve
            from services.scheduler import task_scheduler
            assert not task_scheduler.is_running()
            print(f'PASS: startup/readiness, built UI, {len(paths)+1} HTTP reads, WS bootstrap/snapshot/ping/curve; scheduler stopped')


if __name__ == '__main__':
    main()
