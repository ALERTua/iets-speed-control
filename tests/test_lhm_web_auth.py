"""Basic-auth handling of the LibreHardwareMonitor web provider, against a local stub server.

LibreHardwareMonitor's HttpServer sets AuthenticationSchemes.Basic with realm
"Libre Hardware Monitor" and answers 401 when the credentials do not match, so the stub below
imitates exactly that.
"""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from iets_speed_control.sensors.lhm_web import LibreHardwareMonitorWebProvider, redact

DOCUMENT = {
    "Text": "Sensor",
    "Children": [
        {
            "Text": "STUB-PC",
            "Children": [
                {
                    "Text": "FakeCPU",
                    "Children": [
                        {
                            "Text": "Temperatures",
                            "Children": [
                                {
                                    "Text": "CPU Package",
                                    "Value": "63,0 °C",
                                    "Type": "Temperature",
                                    "SensorId": "/fakecpu/0/temperature/0",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    ],
}

USERNAME = "monitor"
PASSWORD = "s3cret"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.seen_authorization = self.headers.get("Authorization")

        if self.headers.get("Authorization") != self.server.expected_authorization:
            self._respond(401, b"<HTML><HEAD><TITLE>401 Unauthorized</TITLE></HEAD></HTML>", "text/html")
            return

        self._respond(200, json.dumps(DOCUMENT).encode(), "application/json")

    def _respond(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        if status == 401:
            self.send_header("WWW-Authenticate", 'Basic realm="Libre Hardware Monitor"')
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    """A stub LibreHardwareMonitor web server that demands the expected Basic credentials."""
    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    token = base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode("ascii")
    httpd.expected_authorization = f"Basic {token}"
    httpd.seen_authorization = None

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()


@pytest.fixture
def url(server) -> str:
    host, port = server.server_address
    return f"http://{host}:{port}/data.json"


def provider(url, **credentials) -> LibreHardwareMonitorWebProvider:
    credentials.setdefault("username", "")
    credentials.setdefault("password", "")
    return LibreHardwareMonitorWebProvider(url=url, timeout=5.0, **credentials)


def test_correct_credentials_return_temperatures(url):
    temperatures = provider(url, username=USERNAME, password=PASSWORD).get_temperatures()

    assert temperatures == {"FakeCPU/CPU Package": 63.0}


def test_credentials_are_sent_without_waiting_for_a_challenge(url, server):
    provider(url, username=USERNAME, password=PASSWORD).get_temperatures()

    assert server.seen_authorization == server.expected_authorization, (
        "the very first request must already carry the Authorization header"
    )


def test_wrong_credentials_yield_no_temperatures(url, caplog):
    temperatures = provider(url, username=USERNAME, password="wrong").get_temperatures()

    assert temperatures == {}
    assert "rejected the credentials" in caplog.text


def test_missing_credentials_send_no_authorization_header(url, server):
    temperatures = provider(url).get_temperatures()

    assert temperatures == {}
    assert server.seen_authorization is None


def test_unreachable_server_is_reported_without_raising():
    # Port 1 is reserved and never served, so this fails to connect rather than timing out.
    temperatures = provider("http://127.0.0.1:1/data.json").get_temperatures()

    assert temperatures == {}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://user:pass@localhost:8085/data.json", "http://localhost:8085/data.json"),
        ("http://localhost:8085/data.json", "http://localhost:8085/data.json"),
    ],
)
def test_redact_strips_embedded_credentials(raw, expected):
    assert redact(raw) == expected
