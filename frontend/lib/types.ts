// Types mirror the backend API response shapes exactly.
// Field names use snake_case as returned by FastAPI.
// No any. No unknown casts.

// ---------------------------------------------------------------------------
// Session
// ---------------------------------------------------------------------------

export interface CreateSessionResponse {
  session_id: string;
  created_at: string;
}

export interface SessionInfoResponse {
  session_id: string;
  created_at: string;
  last_accessed_at: string;
  inputs_loaded: boolean;
}

// ---------------------------------------------------------------------------
// Input upload
// ---------------------------------------------------------------------------

export interface StudentSummary {
  major: string;
  class_year: number;
  catalog_year: string;
  credits_required: string;
  credits_applied: string;
}

export interface CatalogSummary {
  parent_sections: number;
}

export interface RequirementSummary {
  total_items: number;
  unsatisfied_items: number;
  unmatched_items: string[];
}

export interface InputSummaryResponse {
  session_id: string;
  student_summary: StudentSummary;
  catalog_summary: CatalogSummary;
  requirement_summary: RequirementSummary;
  warnings: string[];
}

// ---------------------------------------------------------------------------
// Schedule generation request
// ---------------------------------------------------------------------------

export interface PreferencesRequest {
  min_credits: string;
  max_credits: string;
  earliest_start?: string | null;
  latest_end?: string | null;
  free_days: string[];
  preferred_subjects: string[];
  excluded_courses: string[];
  lock_preregistered: boolean;
}

export interface GenerateSchedulesRequest {
  preferences: PreferencesRequest;
  locked_ref_nos: string[];
  max_results?: number;
  max_ranked?: number | null;
}

// ---------------------------------------------------------------------------
// Schedule data
// ---------------------------------------------------------------------------

export interface MeetingTime {
  days: string[];
  start: string; // "HH:MM"
  end: string;   // "HH:MM"
}

export interface Section {
  ref_no: string;
  course_code: string;
  section_id: string;
  title: string;
  credits: string;
  course_type: string;
  instructors: string[];
  meeting_times: MeetingTime[];
}

export interface RequirementGain {
  id: string;
  label: string;
  notes: string;
}

export interface ScoreBreakdown {
  requirement_gains: number;
  preferred_subjects: number;
  free_days: number;
  compactness: number;
  credit_load: number;
}

export interface RankedSchedule {
  schedule_id: string;
  parent_sections: Section[];
  linked_sections: Section[];
  total_credits: string;
  score: number;
  score_breakdown: ScoreBreakdown;
  requirement_gains: RequirementGain[];
  category: string;
  explanation: string;
}

export interface SearchMetadata {
  candidate_count: number;
  option_count: number;
  generated_schedules: number;
  solver_cap: number;
  cap_reached: boolean;
  search_space_fully_enumerated: boolean;
}

// ---------------------------------------------------------------------------
// Schedule result responses
// ---------------------------------------------------------------------------

export interface ScheduleResultResponse {
  status: "schedules_found";
  top_schedules: RankedSchedule[];
  categories: Record<string, RankedSchedule[]>;
  search_metadata: SearchMetadata;
}

export interface ConstraintDiagnostic {
  no_valid_schedules: boolean;
  reasons: string[];
  suggested_relaxations: string[];
}

export interface DiagnosticResultResponse {
  status: "no_valid_schedules";
  diagnostic: ConstraintDiagnostic;
  search_metadata: SearchMetadata;
}

export type ScheduleResponse = ScheduleResultResponse | DiagnosticResultResponse;

// ---------------------------------------------------------------------------
// Explanation
// ---------------------------------------------------------------------------

export interface ExplanationResponse {
  schedule_id: string;
  explanation: string;
  source: "provider" | "fallback";
}

// ---------------------------------------------------------------------------
// Ambiguity resolution (409)
// ---------------------------------------------------------------------------

export interface AmbiguousChoiceSection {
  ref_no: string;
  section_id: string;
  meeting_times: MeetingTime[];
}

export interface AmbiguousCourse {
  course_code: string;
  choices: AmbiguousChoiceSection[];
}

// ---------------------------------------------------------------------------
// Error envelope
// ---------------------------------------------------------------------------

export interface ApiErrorDetail {
  code: string;
  message: string;
  details: Record<string, unknown>;
}

export interface ApiErrorEnvelope {
  error: ApiErrorDetail;
}

// Typed error thrown by the API client.
export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: Record<string, unknown>;

  constructor(code: string, message: string, status: number, details: Record<string, unknown> = {}) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

// 409 ambiguity specifically surfaced as a typed error.
export class AmbiguityError extends ApiError {
  readonly courses: AmbiguousCourse[];

  constructor(courses: AmbiguousCourse[]) {
    super(
      "locked_section_selection_required",
      "One or more preregistered courses have multiple catalog sections. Please select the correct section.",
      409,
      {},
    );
    this.name = "AmbiguityError";
    this.courses = courses;
  }
}
