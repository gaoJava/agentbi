"""Authenticated Superset-to-AgentBI bridge.

The browser sends only dashboard state. Identity and roles are derived from Superset's
server-side session so a caller cannot grant itself additional data access.
"""

from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import Response, request
from flask_appbuilder.api import expose, permission_name, protect, safe
from flask_login import current_user
from superset_core.rest_api.api import RestApi
from superset_core.rest_api.decorators import api

from .validation import SupersetContextAdapter, sanitize_context

_MAX_UPSTREAM_RESPONSE_BYTES = 2_000_000
_CLIENT_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


@api(
    id="insight_pilot_api",
    name="InsightPilot AgentBI API",
    description="Authenticated proxy to the AgentBI orchestration service",
)
class InsightPilotAPI(RestApi):
    openapi_spec_tag = "InsightPilot AgentBI"
    class_permission_name = "insight_pilot"

    @expose("/analyze", methods=("POST",))
    @protect()
    @safe
    @permission_name("read")
    def analyze(self) -> Response:
        """Analyze the visible dashboard context.
        ---
        post:
          description: Send a governed dashboard analysis request.
          responses:
            200:
              description: Analysis with query evidence
            400:
              description: Invalid request
            502:
              description: AgentBI service unavailable
        """
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return self.response_400(message="question and context are required")

        question = body.get("question")
        if not isinstance(question, str) or not 2 <= len(question.strip()) <= 2000:
            return self.response_400(message="question length must be between 2 and 2000")

        try:
            context = sanitize_context(body.get("context"))
            dashboard_context = SupersetContextAdapter().adapt(body.get("context"))
        except (TypeError, ValueError) as exc:
            return self.response_400(message=str(exc))

        roles = [str(role.name)[:128] for role in list(getattr(current_user, "roles", []))[:50]]
        payload = {
            "question": question,
            # ScreenContext remains the legacy evidence/display envelope.
            # Dashboard-only hints are sent separately after host adaptation.
            "context": {key: value for key, value in context.items() if key != "focused_metric"},
            "dashboard_context": dashboard_context,
            "actor": {
                "subject": str(current_user.get_id()),
                "display_name": str(getattr(current_user, "first_name", ""))[:128] or None,
                "roles": roles,
            },
        }
        for optional_id in ("chat_id", "agent_id"):
            value = body.get(optional_id)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                payload[optional_id] = value
        client_request_id = body.get("client_request_id")
        if isinstance(client_request_id, str) and _CLIENT_REQUEST_ID.fullmatch(client_request_id):
            payload["client_request_id"] = client_request_id
        try:
            result = self._post_to_orchestrator(payload)
        except (TypeError, ValueError) as exc:
            return self.response_500(message=str(exc))
        except HTTPError as exc:
            # Preserve only actionable status classes; never relay upstream bodies.
            if exc.code in {400, 403, 429}:
                return self.response(exc.code, message="AgentBI request rejected")
            return self.response(502, message="AgentBI service unavailable")
        except TimeoutError:
            return self.response(504, message="AgentBI request timed out")
        except URLError:
            # Do not leak upstream URLs, credentials, or response bodies.
            return self.response(502, message="AgentBI service unavailable")
        return self.response(200, result=result)

    @staticmethod
    def _post_to_orchestrator(payload: dict) -> dict:
        base_url = os.getenv("AGENTBI_ORCHESTRATOR_URL", "http://127.0.0.1:8090").rstrip("/")
        api_key = os.getenv("AGENTBI_API_KEY", "")
        if len(api_key) < 32:
            raise ValueError("AgentBI server credential is not configured")
        upstream = Request(
            f"{base_url}/api/v1/analyze",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-AgentBI-Key": api_key,
            },
            method="POST",
        )
        # The URL is administrator-controlled and never accepts browser input.
        with urlopen(upstream, timeout=25) as response:
            raw = response.read(_MAX_UPSTREAM_RESPONSE_BYTES + 1)
        if len(raw) > _MAX_UPSTREAM_RESPONSE_BYTES:
            raise ValueError("AgentBI service returned an oversized response")
        result = json.loads(raw.decode("utf-8"))
        if not isinstance(result, dict):
            raise TypeError("AgentBI service returned an invalid response")
        return result
