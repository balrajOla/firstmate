import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";

export function client({ agent = false, bin = process.env.OLYMPUS_BIN || "olympus", home = process.env.OLYMPUS_HOME, tokenFile = agent ? process.env.OLYMPUS_AGENT_TOKEN_FILE : process.env.OLYMPUS_TOKEN_FILE } = {}) {
  return (request) => new Promise((resolve, reject) => {
    if (agent && !tokenFile) return reject(new Error("Assign OLYMPUS_AGENT_TOKEN_FILE to the chief's scoped credential. Operator credentials are never used for model tools."));
    const args = [...(home ? ["--home", home] : []), ...(tokenFile ? ["--token-file", tokenFile] : []), "request", "-"];
    const child = execFile(bin, args, { timeout: 22000, maxBuffer: 8 * 1024 * 1024 }, (error, stdout, stderr) => {
      if (error) reject(new Error(stderr.trim() || error.message));
      else { try { resolve(JSON.parse(stdout)); } catch { reject(new Error("Coordinator response was not valid JSON")); } }
    });
    child.stdin.on("error", () => {});
    child.stdin.end(JSON.stringify({ request_id: randomUUID(), ...request }));
  });
}
