// Intake is independent of the chief's model turn. Journal before network writes
// and retain the same request id for any explicit retry of a submitted request.
import { randomUUID } from "node:crypto";
export class ParallelIntake {
  constructor(read, journal = () => {}) { this.read = read; this.journal = journal; this.records = new Map(); }
  restore(record) { if (record?.id && record?.text) this.records.set(record.id, record); }
  save(record) { this.records.set(record.id, record); this.journal({ ...record }); return record; }
  async receive(text, project, referenceTask, references = []) {
    const record = this.save({ id: "intake-" + randomUUID(), text, project, references, state: "received", received: new Date().toISOString() });
    if (references.some(r => !r.task)) return this.save({ ...record, state: "needs_reference" });
    try {
      if (referenceTask) project = (await this.read({ action: "task.get", task: referenceTask })).task.project;
      const preview = await this.read({ action: "intake.preview", text, ...(project ? { project } : {}) });
      if (preview.route === "needs_clarification") return this.save({ ...record, state: "needs_project", candidates: preview.candidates || [] });
      const selected = project || (preview.route === "existing_project" ? preview.candidates?.[0]?.id : undefined);
      return await this.submit(record, selected);
    } catch (error) { return this.save({ ...record, state: "needs_reconciliation", error: String(error.message || error) }); }
  }
  async submit(record, project) {
    const context = record.references?.length ? "\n\nCanonical workstation task references (not permissions): " + record.references.map(r => `${r.label} = ${r.task}`).join(", ") : "";
    const request = { action: "intake.submit", text: record.text + context, request_id: record.id, ...(project ? { project } : {}) };
    this.save({ ...record, project, state: "submitting", request });
    try {
      const result = await this.read(request);
      return this.save({ ...record, project: result.project || project, task: result.task, state: "accepted", duplicate: Boolean(result.duplicate), request });
    } catch (error) { return this.save({ ...record, project, state: "needs_reconciliation", request, error: String(error.message || error) }); }
  }
  pending() { return [...this.records.values()].filter(r => r.state !== "accepted"); }
}
