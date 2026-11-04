// The workstation chief has normal Pi tools. Operator-only broker mutations
// still require explicit interactive confirmation in olympus_control.
export const chiefTools = ["olympus_control", "olympus_help", "olympus_summary"];
export const workstationTools = ["read", "bash", "edit", "write", "grep", "find", "ls", "codemode", "tool_search"];
export function chiefToolPolicy() { return undefined; }
