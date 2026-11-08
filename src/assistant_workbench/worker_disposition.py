"""Record a Paperclip worker's own task disposition.

Paperclip asks a worker to close its task through the API with its injected
PAPERCLIP_* settings. Claude Code refuses commands that expand environment
variables, so without a permission bypass the worker cannot do that. This
command reads the settings itself, never prints the token and changes only
the status of the task that woke the run.

Usage: python -m assistant_workbench.worker_disposition done
       python -m assistant_workbench.worker_disposition blocked "what would unblock it"
"""

import os
import sys

from .paperclip import CoordinatorError, Paperclip
from .util import WorkbenchError

DISPOSITIONS = ("done", "blocked")
SETTINGS = ("PAPERCLIP_API_URL", "PAPERCLIP_API_KEY", "PAPERCLIP_RUN_ID", "PAPERCLIP_TASK_ID")


def main(argv=None, environ=None):
    argv = sys.argv[1:] if argv is None else argv
    environ = os.environ if environ is None else environ
    if not argv or argv[0] not in DISPOSITIONS or len(argv) > (2 if argv[0] == "blocked" else 1):
        print('usage: worker_disposition done | blocked "what would unblock it"', file=sys.stderr)
        return 2
    needed = SETTINGS + (("PAPERCLIP_AGENT_ID",) if argv[0] == "blocked" else ())
    missing = [k for k in needed if not environ.get(k)]
    if missing:
        print("Paperclip run settings are missing: " + ", ".join(missing), file=sys.stderr)
        return 1
    body = {"status": argv[0]}
    if argv[0] == "blocked":
        # Paperclip accepts blocked only with an unblock path, and an agent
        # may name only itself as its owner; Paperclip wakes it once with
        # the action, and the Workbench leaves the decision to the operator.
        reason = (argv[1] if len(argv) > 1 else "").strip()
        body["unblockDescriptor"] = {
            "owner": {"agentId": environ["PAPERCLIP_AGENT_ID"]},
            "action": reason[:2000] or "The worker could not complete this task; see its final report.",
        }
    url = environ["PAPERCLIP_API_URL"].rstrip("/")
    if url.endswith("/api"):
        url = url[: -len("/api")]
    try:
        api = Paperclip({"url": url, "token_env": "PAPERCLIP_API_KEY"})
        api.token = environ["PAPERCLIP_API_KEY"]
        # Paperclip links agent mutations to the run that made them.
        api.headers["X-Paperclip-Run-Id"] = environ["PAPERCLIP_RUN_ID"]
        api.update_issue(environ["PAPERCLIP_TASK_ID"], body)
    except (CoordinatorError, WorkbenchError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("Recorded task disposition: " + argv[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
