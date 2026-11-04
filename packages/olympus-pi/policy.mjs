// Chief sessions coordinate through the broker; project shells belong to admitted workers.
export const chiefTools = ["olympus_control", "olympus_help", "olympus_summary"];
export function chiefToolPolicy(name, enabled) {
  if (enabled && !chiefTools.includes(name)) return { block: true, reason: "This chief session uses broker tools only. Delegate project execution to an admitted worker." };
}
