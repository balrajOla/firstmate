# Replacing Olympus without losing local work

The final active system contains no Olympus daemon, launcher or configuration include. Recovery archives are retained separately and are not used by Workbench. Historical Git branches are not runtime dependencies.

## Inventory and preservation

Run `awb migrate discover` on the actual account. It checks known prefix/state/service locations and reports shared shell/WezTerm lines mentioning Olympus. Add custom install paths explicitly. Discovery does not scan unrelated conversations or projects and does not change anything.

Stop writes to legacy state before taking the final snapshot. Identify services from their actual installed definitions, executable paths and PIDs. Do not kill processes just because their names contain Olympus. Preserve unrelated terminal settings, official harness credentials, repositories, agents' produced artifacts and authorized existing schedules.

```sh
awb migrate backup /private/recovery/new-snapshot /exact/legacy/root /exact/legacy/launcher
awb migrate verify /private/recovery/new-snapshot
```

The backup includes tracked, modified, untracked and ignored files, permissions and symlinks. Git roots receive a history bundle in addition to the working copy. Linked worktrees therefore retain history even when their `.git` file points outside the copied root. Symlink targets are not traversed or deleted. The destination must be new, private, outside the selected roots and preferably on the same filesystem for atomic retirement.

## Local modifications

Create a private reconciliation report. For each modification, record one of: already provided by Workbench, ported with a test, preserved as project data, or deliberately retired with the user's existing authorization. Do not copy the old application wholesale into the new runtime. Check old schedules, shell aliases, key bindings, project routing, saved context and environment setup. Secrets must stay private and must not enter the GitHub repository.

If an old root contains nested project repositories, extract them to preserved project locations and verify their histories/working trees before retiring that root. The retirement command refuses to move such a root as if it were disposable application code.

## Qualify the target installation

Use the installed launcher in a new prefix/state directory. Test the coordinator, at least two actual signed-in workers, their messages and artifacts, independent review, context recovery across five fresh sessions, cancellation, reports, the actual WezTerm GUI and a fresh-location restore. Also complete the required research, quality and soak gates for the claimed release profile.

Record evidence through `qualification.record`. An operator attestation must reference retained files; changing an evidence file invalidates the report. Screenshots must come from the actual target app, not a synthetic drawing or the headless SVG shipped here. A missing integration must remain visible and blocks a full-profile release.

After the gates pass and identified legacy services are stopped, issue a cutover certificate through `qualification.cutover`, supplying the migration backup and local-change reconciliation report. Then:

```sh
awb migrate retire /private/recovery/new-snapshot --qualification /private/recovery/cutover.json
```

Retirement verifies that roots have not changed since backup and moves them out of active paths into the private recovery directory. It does not recursively delete user data. It is resumable after an interrupted move and refuses conflicting destinations. Cross-filesystem moves can fail safely; use a recovery directory on the same filesystem for cutover.

Shared `.wezterm.lua`, shell and harness config files require an exact diff: remove only verified Olympus-owned lines and includes. The generic retirement command never treats an entire shared config file as disposable. Reload the target terminal and service definitions, then prove that no Olympus service restarts and Workbench works independently.

## Recovery

`awb migrate restore BACKUP` restores absent legacy roots from verified copies and refuses to overwrite newer files. It does not automatically start legacy services. Reconcile ownership before restoring service operation. Workbench state has separate `awb backup DEST` and `awb restore SOURCE NEW_DEST` commands.

`awb install --source SOURCE --prefix PREFIX` builds a content-addressed release and points the owned launcher at it; reinstalling the same source changes nothing and keeps the recorded previous release. Each build step's output goes to the release's `install.log`. If a step fails (for example, pip cannot reach its index), the launcher keeps the active release, the attempt is renamed to `<release>.failed-<id>` with its log, and the error names that log; fix the cause and run the same command again. `awb rollback --prefix PREFIX` points the launcher back at that previous release after checking it is complete and starts; running it again returns to the newer release. A release older than this command cannot roll itself forward, so use the newer installer for that. `awb uninstall --prefix PREFIX` removes the owned Workbench launcher and preserves data/releases. Stop the owned service first and remove its exact installed definition. `awb terminal uninstall` removes only the exact managed include, preserving unrelated later terminal edits. Release-directory cleanup is intentionally manual and ownership-checked; user repositories are never an uninstall target.
