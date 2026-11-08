# Optional context backends

Workbench can copy an archived project source into Hindsight or OpenViking,
recall related material, read back the original text, and delete the managed
copy. The local archive remains the source of record. A recalled memory or
summary is labelled as derived material and requires review before citation.

These integrations are optional. The default project archive and search work
without either service. The adapters make real HTTP requests; the automated
tests use local contract servers and do not certify an installed service or its
model quality.

## Configuration

Install and configure the chosen service using its upstream instructions first.
Merge its entry into `integrations` in the Workbench `config.json`. Replace the
project key with the ID returned by `awb project list`. Do not reuse a bank or
overlapping directory for different projects.

```json
{
  "integrations": {
    "hindsight": {
      "enabled": true,
      "url": "http://127.0.0.1:8888",
      "auth": "local",
      "allow_write": true,
      "timeout_seconds": 120,
      "projects": {
        "proj_replace_me": {"bank_id": "awb-example"}
      }
    },
    "openviking": {
      "enabled": true,
      "url": "http://127.0.0.1:1933",
      "auth": "subscription",
      "allow_write": true,
      "timeout_seconds": 120,
      "projects": {
        "proj_replace_me": {
          "root_uri": "viking://resources/workbench/example"
        }
      }
    }
  }
}
```

`auth` declares how the external service obtains inference: `local`,
`subscription`, or `api`. It does not configure that provider. Both services
must have a working model and, where needed, embedding configuration before
retaining or recalling material.

Hindsight currently documents local Ollama, LM Studio and llama.cpp providers,
and subscription providers including Codex and Claude Code. OpenViking
documents Codex OAuth and local Ollama configurations. Use the provider setup
supported by your installed release; a signed-in Workbench harness does not
automatically authenticate a separate service. Workbench never reads harness
credential files or turns a subscription login into an API key.

A remote service requires HTTPS and an explicit service token. Add
`"token_env": "AWB_HINDSIGHT_TOKEN"` or
`"token_env": "AWB_OPENVIKING_TOKEN"`, then set that variable outside the
repository. This is the backend's access credential, separate from its model
provider credential. Loopback services may omit it. A configured missing token
always blocks access. The adapter sends Hindsight tokens as Bearer credentials
and OpenViking tokens in `X-API-Key`.

URLs must identify one origin: no credentials, paths, query strings or redirects.
Unencrypted HTTP is restricted to loopback. Bank names and resource roots are
validated; project directories cannot overlap, including case-only aliases.

## Actions

The application exposes these actions through the same request dispatcher used
by the CLI, local server and MCP tool.

| Action | Required arguments | Result |
|---|---|---|
| `backend.ingest` | `backend`, `project`, `source_id` | Stores one archived source and a durable receipt |
| `backend.recall` | `backend`, `project`, `query`; optional `limit` | Related derived material linked to managed originals |
| `backend.read` | `backend`, `project`, `source_id` | Original text, checked against the archived text hash |
| `backend.delete` | `backend`, `project`, `source_id` | Deletes only the managed remote object |
| `backend.reconcile` | `backend`, `project`, `source_id` | Reads back an uncertain operation before a retry |
| `backend.status` | Optional `project` | Receipts and outstanding deletion or indexing work |

For example, after `context.ingest` returns a source ID:

```bash
awb request '{"action":"backend.ingest","args":{"backend":"hindsight","project":"proj_replace_me","source_id":"src_replace_me"}}'
awb request '{"action":"backend.recall","args":{"backend":"hindsight","project":"proj_replace_me","query":"Which revenue exclusions apply?","limit":8}}'
```

Copies require `allow_write: true` and ownership of the source. A grant to read
another project's archive does not authorize copying it into a different bank.
There is no automatic upload or fallback when a backend is unavailable.

## Contracts and recovery

| Operation | Hindsight HTTP contract | OpenViking HTTP contract |
|---|---|---|
| Ingest | `POST /v1/default/banks/{bank}/memories`, synchronous, document ID equals source ID | `POST /api/v1/content/write`, replace the source-ID `.md` file, wait for refresh |
| Recall | `POST .../memories/recall`, project tag required | `POST /api/v1/search/find`, resource root and project tag required |
| Read | `GET .../documents/{document}`, verify bank and document ID | `GET /api/v1/content/read`, exact managed URI |
| Delete | `DELETE .../documents/{document}` | `DELETE /api/v1/fs`, exact managed URI, nonrecursive |

Receipts record project, backend, remote object, destination fingerprint, hashes,
state and time. They contain no source text or credentials. Changing the endpoint
or project mapping cannot redirect an old receipt's read or deletion: restore
the previous mapping and clear it before migrating.

Repeated confirmed ingestion does not repeat the network write. Concurrent
ingestion reserves one receipt before sending. A timeout, server error or
unexpected acknowledgement leaves an uncertain state; the next ingest is
blocked until explicit reconciliation. Reconciliation cannot interrupt a
mutation's local request lease. A crashed request becomes eligible after its
configured timeout plus five seconds. A remote server may still have background
work: readback proves stored bytes, not complete extraction or indexing.

OpenViking's acknowledgement distinguishes complete refresh from pending or
skipped indexing. An `index_pending` receipt is not full readiness. Hindsight's
synchronous retention acknowledgement is not proof that extracted facts are
correct. Retrieval returns only items whose document ID or URI maps to a
managed, accessible original in the same project. Consolidated memories without
that direct reference and directory summaries are omitted. Revoked, purged or
superseded sources are also omitted. Raw ranking scores are preserved without treating
them as probabilities.

Readback must match the archived text exactly. OpenViking can sanitize its normal
read output; a mismatch prompts review of the local original. The adapter does
not request restoration of redacted secrets. Each recalled item includes its
original hash, source authority, `derived: true` and `review_required: true`.

## Retention and limits

Local purge and remote deletion are separate operations. `backend.status`
retains remote deletion receipts even after local source text is purged.
Retention workflows should enumerate those receipts, call `backend.delete`, and
retain failed or uncertain deletions for reconciliation. A remote 404 is treated
as already deleted. This acknowledgement does not erase unrelated service
backups, exported reports or independent copies.

Current bounds are 1 MiB of source text per ingest, 2 MiB per JSON request,
4 MiB per JSON response, 1–100 recall results and a 1–120 second request timeout.
Inventory and split larger sources before ingestion; the adapter does not
silently truncate them. Hindsight retain jobs longer than the timeout require
reconciliation. Workbench does not provision either service, validate provider
licensing, audit backend ACLs or promise unlimited context or error-free memory.

## Validation

Run `python -m pytest -q tests/test_context_backends.py`. These tests use real
loopback HTTP with deterministic contract responses. They cover both adapters,
provenance, readback changes, deleted and revoked sources, duplicate and
concurrent writes, uncertain mutations, malformed responses, incomplete
indexing, configuration errors, scope collisions, credentials, redirects and
response limits. They are not live backend/model tests.

Before marking an installed backend qualified, use a disposable project and
known source: retain it, retrieve related facts, compare readback with the
archive, confirm isolation from another project, revoke it, delete its remote
copy, and verify absence. Record backend version, model/provider configuration,
commands, actual responses and failures. Never use contract fixtures as that
qualification record.

## Primary references

API contracts reviewed on 2026-11-08. Verify compatibility when upgrading:

- [Hindsight project and provider setup](https://github.com/vectorize-io/hindsight)
- [Hindsight published OpenAPI](https://api.hindsight.vectorize.io/openapi.json)
- [Hindsight retain](https://docs.hindsight.vectorize.io/api-reference/retain-memories/)
- [Hindsight get document](https://docs.hindsight.vectorize.io/api-reference/get-document/)
- [Hindsight delete document](https://docs.hindsight.vectorize.io/api-reference/delete-document/)
- [OpenViking project and model configuration](https://github.com/volcengine/OpenViking)
- [OpenViking content API](https://docs.openviking.ai/en/api/12-content)
- [OpenViking retrieval API](https://docs.openviking.ai/en/api/06-retrieval)
- [OpenViking filesystem API](https://docs.openviking.ai/en/api/03-filesystem)
