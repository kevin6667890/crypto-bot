export type WorkspaceAiPrimaryState = "CURRENT_VALID" | "LATEST_FAILED" | "LATEST_PENDING" | "STALE_VALID" | "NO_VALID_REPORT";

export function workspaceAiPrimaryState(value: {
  primary_state?: WorkspaceAiPrimaryState;
  display_eligible?: boolean;
  status?: string;
  latest_generated?: { eligibility?: string };
} | null | undefined): WorkspaceAiPrimaryState {
  if (value?.primary_state) return value.primary_state;
  if (value?.latest_generated?.eligibility === "AUDIT_FAILED") return "LATEST_FAILED";
  if (["AUDIT_PENDING", "AUDIT_NOT_FOUND"].includes(String(value?.latest_generated?.eligibility))) return "LATEST_PENDING";
  if (value?.display_eligible && value.status === "CURRENT_AUDITED_REPORT") return "CURRENT_VALID";
  if (value?.display_eligible && value.status === "STALE_AUDITED_REPORT") return "STALE_VALID";
  return "NO_VALID_REPORT";
}
