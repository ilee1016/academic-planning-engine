import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  createSession,
  deleteSession,
  generateSchedules,
  getScheduleExplanation,
  uploadInputs,
} from "@/lib/api";
import { ApiError, AmbiguityError } from "@/lib/types";

// Mock fetch globally.
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

// Set env var.
vi.stubEnv("NEXT_PUBLIC_API_URL", "http://localhost:8000");

function mockResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

beforeEach(() => {
  mockFetch.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("createSession", () => {
  it("calls POST /api/session and returns session_id", async () => {
    mockFetch.mockResolvedValueOnce(
      mockResponse({ session_id: "abc123", created_at: "2026-01-01T00:00:00Z" }),
    );
    const result = await createSession();
    expect(result.session_id).toBe("abc123");
    expect(mockFetch).toHaveBeenCalledWith(
      "http://localhost:8000/api/session",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("throws ApiError on non-ok response", async () => {
    mockFetch.mockResolvedValueOnce(
      mockResponse({ error: { code: "server_error", message: "Oops", details: {} } }, 500),
    );
    await expect(createSession()).rejects.toBeInstanceOf(ApiError);
  });
});

describe("deleteSession", () => {
  it("calls DELETE /api/session/:id", async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, status: 204 } as Response);
    await expect(deleteSession("abc")).resolves.toBeUndefined();
    expect(mockFetch).toHaveBeenCalledWith(
      "http://localhost:8000/api/session/abc",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("ignores 404 (session already gone)", async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 404 } as Response);
    await expect(deleteSession("gone")).resolves.toBeUndefined();
  });
});

describe("uploadInputs", () => {
  it("calls POST /inputs with multipart form and returns summary", async () => {
    const summary = {
      session_id: "abc",
      student_summary: { major: "CS", class_year: 2027, catalog_year: "202304", credits_required: "32", credits_applied: "20" },
      catalog_summary: { parent_sections: 469 },
      requirement_summary: { total_items: 10, unsatisfied_items: 4, unmatched_items: [] },
      warnings: [],
    };
    mockFetch.mockResolvedValueOnce(mockResponse(summary));
    const audit = new File(["pdf"], "audit.pdf", { type: "application/pdf" });
    const catalog = new File(["csv"], "catalog.csv", { type: "text/csv" });
    const result = await uploadInputs("abc", audit, catalog);
    expect(result.student_summary.major).toBe("CS");
  });

  it("throws ApiError with audit_parse_failed code", async () => {
    mockFetch.mockResolvedValueOnce(
      mockResponse({ error: { code: "audit_parse_failed", message: "bad pdf", details: {} } }, 422),
    );
    const audit = new File(["bad"], "audit.pdf");
    const catalog = new File(["csv"], "catalog.csv");
    const err = await uploadInputs("abc", audit, catalog).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("audit_parse_failed");
  });
});

describe("generateSchedules", () => {
  it("returns schedules_found result", async () => {
    const body = {
      status: "schedules_found",
      top_schedules: [],
      categories: {},
      search_metadata: { candidate_count: 5, option_count: 5, generated_schedules: 3, solver_cap: 500, cap_reached: false, search_space_fully_enumerated: true },
    };
    mockFetch.mockResolvedValueOnce(mockResponse(body));
    const result = await generateSchedules("abc", {
      preferences: { min_credits: "3", max_credits: "4", free_days: [], preferred_subjects: [], excluded_courses: [], lock_preregistered: true },
      locked_ref_nos: [],
    });
    expect(result.status).toBe("schedules_found");
  });

  it("throws AmbiguityError for 409 with locked_section_selection_required", async () => {
    const courses = [{ course_code: "CPSC 031", choices: [{ ref_no: "12345", section_id: "01", meeting_times: [] }] }];
    mockFetch.mockResolvedValueOnce(
      mockResponse({
        error: {
          code: "locked_section_selection_required",
          message: "ambiguous",
          details: { courses },
        },
      }, 409),
    );
    const err = await generateSchedules("abc", {
      preferences: { min_credits: "3", max_credits: "4", free_days: [], preferred_subjects: [], excluded_courses: [], lock_preregistered: true },
      locked_ref_nos: [],
    }).catch((e) => e);
    expect(err).toBeInstanceOf(AmbiguityError);
    expect((err as AmbiguityError).courses[0].course_code).toBe("CPSC 031");
  });

  it("returns no_valid_schedules result", async () => {
    const body = {
      status: "no_valid_schedules",
      diagnostic: { no_valid_schedules: true, reasons: ["Too restrictive"], suggested_relaxations: ["Allow more days"] },
      search_metadata: { candidate_count: 0, option_count: 0, generated_schedules: 0, solver_cap: 500, cap_reached: false, search_space_fully_enumerated: true },
    };
    mockFetch.mockResolvedValueOnce(mockResponse(body));
    const result = await generateSchedules("abc", {
      preferences: { min_credits: "3", max_credits: "4", free_days: ["M", "T", "W", "R", "F"], preferred_subjects: [], excluded_courses: [], lock_preregistered: false },
      locked_ref_nos: [],
    });
    expect(result.status).toBe("no_valid_schedules");
  });

  it("throws ApiError on network-level failure", async () => {
    mockFetch.mockRejectedValueOnce(new TypeError("network error"));
    await expect(
      generateSchedules("abc", {
        preferences: { min_credits: "3", max_credits: "4", free_days: [], preferred_subjects: [], excluded_courses: [], lock_preregistered: true },
        locked_ref_nos: [],
      }),
    ).rejects.toBeInstanceOf(TypeError);
  });
});

describe("getScheduleExplanation", () => {
  it("returns explanation with source", async () => {
    mockFetch.mockResolvedValueOnce(
      mockResponse({ schedule_id: "deadbeef", explanation: "This schedule covers CS major.", source: "fallback" }),
    );
    const result = await getScheduleExplanation("session123", "deadbeef");
    expect(result.explanation).toContain("CS major");
    expect(result.source).toBe("fallback");
  });

  it("calls POST on the explanation endpoint", async () => {
    mockFetch.mockResolvedValueOnce(
      mockResponse({ schedule_id: "id1", explanation: "text", source: "provider" }),
    );
    await getScheduleExplanation("s1", "id1");
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("/s1/schedules/id1/explanation"),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("throws ApiError on 404", async () => {
    mockFetch.mockResolvedValueOnce(
      mockResponse({ error: { code: "schedule_not_found", message: "not found", details: {} } }, 404),
    );
    await expect(getScheduleExplanation("s1", "bad")).rejects.toBeInstanceOf(ApiError);
  });
});

describe("API base URL configuration", () => {
  it("uses NEXT_PUBLIC_API_URL from env", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://custom:9000");
    mockFetch.mockResolvedValueOnce(
      mockResponse({ session_id: "x", created_at: "2026-01-01T00:00:00Z" }),
    );
    await createSession();
    expect(mockFetch).toHaveBeenCalledWith(
      "http://custom:9000/api/session",
      expect.anything(),
    );
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://localhost:8000");
  });
});
