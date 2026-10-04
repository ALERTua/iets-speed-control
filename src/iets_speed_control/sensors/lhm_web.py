"""LibreHardwareMonitor temperature source over its built-in web server.

Works where the WMI provider does not (LibreHardwareMonitor only registers its WMI namespace on
some systems). Enable it in LibreHardwareMonitor: Options -> Remote Web Server -> Run.

Labels are built as "<hardware>/<sensor>" because sensor names alone are neither descriptive
(LibreHardwareMonitor reports "Composite Temperature", not "SSD Composite Temperature") nor
unique (two SSDs both report "Temperature #1").
"""

import base64
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request

from ..util.config import CONFIG
from .base import is_live_temperature, to_temperature

logger = logging.getLogger(__name__)

# "63,0 °C" / "63.0 °C" / "-5 °C" -- LibreHardwareMonitor formats numbers in the system locale.
VALUE_PATTERN = re.compile(r"^\s*(-?\d+(?:[.,]\d+)?)")


class LibreHardwareMonitorWebProvider:
    """Reads /data.json from the LibreHardwareMonitor remote web server."""

    name = "lhm-web"
    label = "LibreHardwareMonitor (web server)"
    SENSOR_TYPE = "Temperature"

    def __init__(
        self,
        url: str | None = None,
        timeout: float | None = None,
        username: str | None = None,
        password: str | None = None,
    ):
        # Arguments are kept as overrides rather than resolved once. Everything not overridden is
        # read from the configuration at request time, so editing the URL or the credentials in the
        # settings panel takes effect on the next reading instead of needing a new provider.
        self._url = url
        self._timeout = timeout
        self._username = username
        self._password = password

    @property
    def url(self) -> str:
        return self._url or CONFIG.sensors.lhm_web.url

    @property
    def timeout(self) -> float:
        return CONFIG.sensors.lhm_web.timeout if self._timeout is None else self._timeout

    @property
    def username(self) -> str:
        return CONFIG.sensors.lhm_web.username if self._username is None else self._username

    @property
    def password(self) -> str:
        return CONFIG.sensors.lhm_web.password if self._password is None else self._password

    def get_temperatures(self) -> dict[str, float]:
        output: dict[str, float] = {}
        try:
            with urllib.request.urlopen(self._build_request(), timeout=self.timeout) as response:
                document = json.load(response)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                logger.error(
                    f"LibreHardwareMonitor at {redact(self.url)} rejected the credentials."
                    " Set sensors.lhm_web.username and sensors.lhm_web.password to match"
                    " Options -> Remote Web Server -> Authentication."
                )
            else:
                logger.error(f"LibreHardwareMonitor at {redact(self.url)} returned HTTP {e.code}: {e.reason}")
            return output
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            logger.error(
                f"Error connecting to the LibreHardwareMonitor web server at {redact(self.url)}: {e}."
                " Is it enabled under Options -> Remote Web Server?"
            )
            return output

        self._collect(document, [], output)
        return output

    def _build_request(self) -> urllib.request.Request:
        """Build the request, sending Basic credentials up front rather than after a 401."""
        request = urllib.request.Request(self.url)
        if self.username or self.password:
            token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode("ascii")
            request.add_header("Authorization", f"Basic {token}")

        return request

    def _collect(self, node: dict, trail: list[str], output: dict[str, float]):
        children = node.get("Children") or []
        if children:
            trail = trail + [node.get("Text", "")]
            for child in children:
                self._collect(child, trail, output)
            return

        if node.get("Type") != self.SENSOR_TYPE or not is_live_temperature(node.get("Text", "")):
            return

        temperature = parse_value(node.get("Value"))
        if temperature is None:
            return

        # trail is [root, computer, hardware, category]; the category ("Temperatures") is noise.
        hardware = trail[-2] if len(trail) >= 2 else ""
        sensor = node.get("Text", "")
        label = f"{hardware}/{sensor}" if hardware else sensor
        if label in output:
            label = f"{label} ({node.get('SensorId', '')})"

        output[label] = temperature


def parse_value(raw) -> float | None:
    """Turn a displayed value such as "63,0 °C" into 63.0, or None when there is no number."""
    if raw is None:
        return None

    match = VALUE_PATTERN.match(str(raw))
    if not match:
        return None

    return to_temperature(match.group(1).replace(",", "."))


def redact(url: str) -> str:
    """Drop any user:password embedded in the URL so it never reaches the log."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return "<malformed url>"

    if not parts.username and not parts.password:
        return url

    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"

    return urllib.parse.urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))
