// One bounded chief wake at a time; repeated unchanged conditions never create a loop.
export class WakeGate {
  constructor({ limit = 10, interval = 30000 } = {}) { this.limit = limit; this.interval = interval; this.reset(); }
  reset() { this.count = 0; this.last = -Infinity; this.fingerprint = ""; }
  consider(briefing, inbox, now = Date.now()) {
    const ready = briefing.projects.some(p => p.ready > 0);
    if (!ready && !briefing.exceptions.length && !inbox.length && !briefing.due) return false;
    const key = JSON.stringify([briefing.work_cursor, briefing.due, briefing.projects, briefing.exceptions, inbox.map(m => [m.id,m.state])]);
    if (this.count >= this.limit || now - this.last < this.interval || key === this.fingerprint) return false;
    this.fingerprint = key; this.last = now; this.count++; return true;
  }
}
