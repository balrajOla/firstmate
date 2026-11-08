# Security and correctness boundaries

This is an experimental single-user local application. The CLI, MCP process, configuration and loopback HTTP token have operator authority. Anyone running as the same OS user can read its files. Project filters and worktrees are useful boundaries but are not a hostile-agent sandbox.

- HTTP binds to loopback, requires a bearer token except for health, rejects browser Origin requests, caps request size and concurrent connections, and does not follow credential-bearing redirects.
- Coordinator URLs cannot embed credentials. Non-loopback coordinator HTTP is rejected; remote access requires HTTPS and an explicit token environment binding.
- Original evidence is hashed; missing, altered, revoked or out-of-scope evidence blocks its use. Source text is data, not authority to change permissions.
- External effects have idempotency keys and uncertain states. This does not turn arbitrary shell side effects into exactly-once operations.
- Checkpoint generations fence mediated commits. An actual old process must be stopped before writable workspace reuse. Terminal status alone is insufficient.
- Paperclip workers run the official CLI on the legacy runner with permission bypass off. Claude workers use `--bare` and accept edits only inside their task workspace. Subscription profiles never pass provider API keys to them. Their only Paperclip API access is a disposition command that sets their own task to done or blocked, without exposing the run token. The bridge refuses bypassing profiles, converted agents and repository issues without an isolated workspace. A Paperclip worktree shares the repository's object store; it is not a sandbox.
- Author/reviewer identity separation is recorded but is operator-controlled. Do not claim cryptographic or organizational independence.
- A deterministic process profile must declare `fixture_only: true`; it cannot qualify as a language model.
- Memory cleanup invalidates live outcomes. External indexes, copied exports and backups require separate deletion and retention verification.
- Migration preserves private files and Git history, refuses changed roots, and removes only selected paths. No process-name kill or recursive global cleanup is performed.

Production qualification requires OS-level isolation appropriate to the trust of workers, scoped coordinator credentials, retained live cancellation evidence, private backup handling and review of upstream installers/provider plugins. Do not expose this as a shared internet service.
