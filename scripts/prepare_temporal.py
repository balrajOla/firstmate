"""Download and start/stop the SDK's official development server for local tests."""

import argparse
import asyncio
import json
from pathlib import Path


async def prepare(directory):
    from temporalio.testing import WorkflowEnvironment

    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    environment = await WorkflowEnvironment.start_local(
        download_dest_dir=str(directory), dev_server_log_level="error"
    )
    try:
        await environment.client.service_client.check_health()
    finally:
        await environment.shutdown()
    binaries = [
        path for path in directory.glob("temporal-sdk-python-*") if path.is_file()
    ]
    if len(binaries) != 1:
        raise RuntimeError("Expected one official SDK development-server binary")
    print(json.dumps({"binary": str(binaries[0]), "service_started_and_stopped": True}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    asyncio.run(prepare(args.directory))
