"""Optional HTTP smoke check; missing dependencies are an explicit skip."""

import importlib.util
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
HAS_HTTP = all(importlib.util.find_spec(name) is not None for name in ("fastapi", "httpx"))


@unittest.skipUnless(HAS_HTTP, "FastAPI / HTTPX are not installed")
class HealthTests(unittest.TestCase):
    def test_process_health(self) -> None:
        from fastapi.testclient import TestClient
        from futurecup.api.app import create_app
        with TestClient(create_app()) as client:
            response = client.get("/api/v1/health")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"status": "ok", "scope": "process"})


if __name__ == "__main__":
    unittest.main()
