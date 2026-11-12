"""HTTP adapter for the pinned Paperclip API. Never launches a competing scheduler."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from .util import WorkbenchError


class CoordinatorError(WorkbenchError):
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


def _error_detail(exc):
    """Paperclip's own short error text, so a refusal explains itself."""
    try:
        error = json.loads(exc.read(4096)).get("error")
    except (OSError, ValueError, AttributeError):
        return ""
    return ": " + error[:300] if isinstance(error, str) and error.strip() else ""


class Paperclip:
    def __init__(self, settings, timeout=20):
        self.settings = settings
        self.base = settings["url"].rstrip("/")
        self.timeout = timeout
        parsed = urllib.parse.urlsplit(self.base)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise WorkbenchError(
                "Coordinator URL must be an HTTP(S) origin without embedded credentials"
            )
        if parsed.scheme == "http" and parsed.hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise WorkbenchError("Remote coordinators require HTTPS")
        self.company = settings.get("company_id")
        self.token = os.environ.get(settings.get("token_env", "AWB_PAPERCLIP_TOKEN"))
        self.headers = {}

    def request(self, method, path, payload=None):
        headers = {"Accept": "application/json", **self.headers}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        data = None if payload is None else json.dumps(payload).encode()
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.base + "/api" + path, data=data, headers=headers, method=method
        )

        # Redirects must not send credentials to an unconfigured destination.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        try:
            with urllib.request.build_opener(NoRedirect()).open(
                req, timeout=self.timeout
            ) as res:
                raw = res.read(10 * 1024 * 1024 + 1)
                if len(raw) > 10 * 1024 * 1024:
                    raise CoordinatorError(
                        "Coordinator response exceeded limit", method != "GET"
                    )
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raise CoordinatorError(
                f"Paperclip returned HTTP {exc.code} for {method} {path}"
                + _error_detail(exc),
                exc.code >= 500 and method != "GET",
            ) from exc
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise CoordinatorError(
                f"Paperclip {method} {path} did not return a confirmed response",
                method != "GET",
            ) from exc

    def company_path(self, suffix):
        if not self.company:
            raise CoordinatorError(
                "Set coordinator.company_id after Paperclip onboarding"
            )
        return "/companies/" + urllib.parse.quote(self.company, safe="") + suffix

    def health(self):
        return self.request("GET", "/health")

    def projects(self):
        return self.request("GET", self.company_path("/projects"))

    def agents(self):
        return self.request("GET", self.company_path("/agents"))

    def create_project(self, body):
        return self.request("POST", self.company_path("/projects"), body)

    def create_agent(self, body):
        return self.request("POST", self.company_path("/agents"), body)

    def terminate_agent(self, identity):
        return self.request(
            "POST", "/agents/" + urllib.parse.quote(identity, safe="") + "/terminate", {}
        )

    def agent_issues(self, identity):
        return self.request(
            "GET",
            self.company_path("/issues?assigneeAgentId=" + urllib.parse.quote(identity, safe="")),
        )

    def create_issue(self, body):
        return self.request("POST", self.company_path("/issues"), body)

    def issue(self, identity):
        return self.request("GET", "/issues/" + urllib.parse.quote(identity, safe=""))

    def update_issue(self, identity, body):
        return self.request(
            "PATCH", "/issues/" + urllib.parse.quote(identity, safe=""), body
        )

    def cancel_run(self, identity):
        return self.request(
            "POST",
            "/heartbeat-runs/" + urllib.parse.quote(identity, safe="") + "/cancel",
            {},
        )

    def resume_agent(self, identity):
        return self.request("POST", "/agents/" + urllib.parse.quote(identity, safe="") + "/resume", {})

    def retry_issue(self, identity, idempotency_key, observed_run):
        """Paperclip's own task retry: refused as stale unless observed_run is
        still the task's latest run, and replayed for the same key."""
        return self.request(
            "POST", "/issues/" + urllib.parse.quote(identity, safe="") + "/retry",
            {"idempotencyKey": idempotency_key, "observedRunId": observed_run},
        )

    def experimental_settings(self):
        return self.request("GET", "/instance/settings/experimental")

    def issue_activity(self, identity):
        return self.request(
            "GET", "/issues/" + urllib.parse.quote(identity, safe="") + "/activity"
        )

    def issue_runs(self, identity):
        return self.request(
            "GET", "/issues/" + urllib.parse.quote(identity, safe="") + "/runs"
        )

    def run(self, identity):
        return self.request(
            "GET", "/heartbeat-runs/" + urllib.parse.quote(identity, safe="")
        )

    def wakeup(self, identity, body):
        return self.request(
            "POST", "/agents/" + urllib.parse.quote(identity, safe="") + "/wakeup", body
        )
