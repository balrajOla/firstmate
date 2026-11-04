// Conversation-local convenience references, never backend identities or authority.
// Persist only additions in Pi's branch metadata; do not recycle cancelled tasks.
export class TaskNumbers {
  constructor() { this.byId = new Map(); this.byNumber = new Map(); this.next = 1; }
  restore(pairs = []) {
    for (const pair of pairs) {
      if (!Array.isArray(pair) || pair.length !== 2) continue;
      const [id, number] = pair;
      if (typeof id !== "string" || !/^T[A-Za-z0-9._-]+$/.test(id) || !Number.isSafeInteger(number) || number < 1) continue;
      if (this.byId.has(id) || this.byNumber.has(number)) continue;
      this.byId.set(id, number); this.byNumber.set(number, id);
      this.next = Math.max(this.next, number + 1);
    }
  }
  assign(tasks = []) {
    const added = [];
    for (const task of [...tasks].sort((a, b) => (a.created || 0) - (b.created || 0) || a.id.localeCompare(b.id))) {
      if (!this.byId.has(task.id)) {
        const number = this.next++;
        this.byId.set(task.id, number); this.byNumber.set(number, task.id);
        added.push([task.id, number]);
      }
    }
    return added;
  }
  label(id) { return this.byId.has(id) ? "#" + this.byId.get(id) : ""; }
  resolve(value) {
    const match = /^#?([1-9]\d*)$/.exec(value.trim());
    return match ? this.byNumber.get(Number(match[1])) : undefined;
  }
  references(prompt = "") {
    const result = [], seen = new Set();
    for (const match of prompt.matchAll(/(?:^|[\s(])#([1-9]\d*)\b/g)) {
      const number = Number(match[1]);
      if (seen.has(number)) continue;
      seen.add(number); result.push({ label: "#" + number, task: this.byNumber.get(number) });
    }
    return result;
  }
}
