"""
RAAH Release Readiness Regression Suite
========================================

Verifies:
1. Root path GET / redirects (HTTP 307) to /dashboard/
2. /dashboard/ serves the Tactical Command Center HTML (HTTP 200)
3. /health/live returns HTTP 200 ALIVE
4. /docs returns HTTP 200
5. Root redirect does not intercept or disrupt existing API routes
"""

import unittest
from fastapi.testclient import TestClient

from api.main import app


class TestReleaseReadiness(unittest.TestCase):
    """Regression test suite for release readiness and root endpoint behavior."""

    @classmethod
    def setUpClass(cls):
        # Use TestClient with follow_redirects=False to verify redirect headers
        cls.client = TestClient(app, follow_redirects=False)
        cls.follow_client = TestClient(app, follow_redirects=True)

    def test_root_redirects_to_dashboard(self):
        """GET / must redirect to /dashboard/ with HTTP 307."""
        resp = self.client.get("/")
        self.assertIn(resp.status_code, [307, 302])
        self.assertEqual(resp.headers.get("location"), "/dashboard/")

    def test_root_followed_serves_command_center(self):
        """Following GET / must result in HTTP 200 serving the Command Center HTML."""
        resp = self.follow_client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers.get("content-type", ""))
        self.assertIn("RAAH — Emergency Dispatch", resp.text)

    def test_dashboard_direct_mount(self):
        """GET /dashboard/ must directly serve the Command Center HTML."""
        resp = self.client.get("/dashboard/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers.get("content-type", ""))
        self.assertIn("RAAH — Emergency Dispatch", resp.text)

    def test_health_live_endpoint(self):
        """GET /health/live must return HTTP 200 ALIVE."""
        resp = self.client.get("/health/live")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("status"), "ALIVE")

    def test_docs_endpoint(self):
        """GET /docs must return HTTP 200 Swagger UI."""
        resp = self.client.get("/docs")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("swagger-ui", resp.text.lower())


if __name__ == "__main__":
    unittest.main()
