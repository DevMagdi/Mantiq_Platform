/**
 * View-models for the screens — every alias points into the *generated* OpenAPI schema.
 * Handwritten DTOs are forbidden (spec §3), so this file only re-exports generated types.
 */
import type { components } from "./schema";

/* AI (M10) — the flags and the note the provider writes are typed from the schema, like everything else. */
export type AiStatus = components["schemas"]["AIStatusOut"];
export type AiSummary = components["schemas"]["AISummarizeOut"];

export type Lead = components["schemas"]["LeadOut"];
export type LeadDetail = components["schemas"]["LeadDetailOut"];
export type LeadActivity = components["schemas"]["LeadActivityOut"];
export type LeadBoardColumn = components["schemas"]["LeadBoardColumnOut"];
export type LeadStats = components["schemas"]["LeadStatsOut"];
export type LeadConvertResult = components["schemas"]["LeadConvertOut"];
export type RepWorkload = components["schemas"]["RepWorkloadOut"];

export type ClientRow = components["schemas"]["ClientOut"];
export type ClientOverview = components["schemas"]["ClientOverviewOut"];
export type ClientKpisRow = components["schemas"]["ClientKpiRowOut"];
export type Contact = components["schemas"]["ContactOut"];
export type InsuredRow = components["schemas"]["InsuredOut"];
export type ImportJob = components["schemas"]["ImportJobOut"];
export type InsuredList = components["schemas"]["InsuredListOut"];
export type InsuredRowList = components["schemas"]["InsuredPageOut"];

export type RequestRow = components["schemas"]["RequestOut"];
export type RequestDetail = components["schemas"]["RequestDetailOut"];
export type RequestMessage = components["schemas"]["RequestMessageOut"];
export type RequestNote = components["schemas"]["RequestNoteOut"];
export type RequestCall = components["schemas"]["CallOut"];
export type RequestAttachment = components["schemas"]["AttachmentOut"];
export type RecordingUrl = components["schemas"]["RecordingUrlOut"];

export type DashboardSummary = components["schemas"]["DashboardSummaryOut"];
export type DashboardChannel = components["schemas"]["DashboardChannelOut"];
export type DashboardStatus = components["schemas"]["DashboardStatusOut"];

export type StaffUser = components["schemas"]["UserOut"];
export type Role = components["schemas"]["RoleOut"];
export type PermissionItem = components["schemas"]["PermissionOut"];
export type FeatureFlag = components["schemas"]["FeatureFlagOut"];
export type Insurer = components["schemas"]["InsurerOut"];
export type InsuranceType = components["schemas"]["InsuranceTypeOut"];
export type LostReason = components["schemas"]["LostReasonOut"];

export type Policy = components["schemas"]["PolicyOut"];
export type InstallmentPlan = components["schemas"]["InstallmentPlanOut"];
export type IssuingCase = components["schemas"]["IssuingCaseOut"];
export type InsurerPerformance = components["schemas"]["InsurerPerformanceOut"];

export type PortalOverview = components["schemas"]["PortalOverviewOut"];
export type PortalRequestItem = components["schemas"]["PortalRequestItemOut"];

export type LeadPage = components["schemas"]["LeadPageOut"];
export type RequestPage = components["schemas"]["RequestPageOut"];
export type ClientPage = components["schemas"]["ClientPageOut"];
export type InsuredPage = components["schemas"]["InsuredPageOut"];
export type AnyPage = components["schemas"]["PageOut"];

/* ── team round: the office roster, the seat the API let us add, and the lead-import report ───────── */
/** A seat in the sales office with the load the manager sees next to it. */
export type TeamMember = components["schemas"]["TeamMemberOut"];
/** What `GET /sales-team/members` answers: the seats plus the roles this screen may hand out. */
export type TeamRoster = components["schemas"]["TeamRosterOut"];
/** The dry-run preview and the final report of one sheet — the same object at both stages. */
export type LeadImportJob = components["schemas"]["LeadImportOut"];
/** The receipt of a bulk redistribution: how many moved, how many per rep, and what was skipped. */
export type BulkAssignResult = components["schemas"]["LeadBulkAssignOut"];
