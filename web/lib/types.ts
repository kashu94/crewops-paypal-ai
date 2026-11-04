/* Result shapes of the tools and endpoints the console reads. Optional where a server may not
   send a field yet. */

export type Level = "high" | "medium" | "low";

export type RiskDriver = { factor: string; points: number; detail: string };
export type Risk = { score: number; level: Level; drivers: RiskDriver[] };

export type Issue = {
  issue_id: string;
  severity: Level;
  severity_reasons: string[];
  kind: string;
  crew_id: string;
  name: string;
  day: string;
  detail: string;
  trip_id?: string;
  rules?: string[];
  suggested_cover?: string;
  suggested_cost_inr?: number;
};

export type Offer = { crew_id: string; name: string; cost_inr: number; answer: string; remark?: string };

export type Callout = {
  callout_id: string;
  trip_id: string;
  seat: string;
  replacing: string;
  status: string;
  asked: Offer[];
  waiting_for?: string;
  answer_by?: string;
  plan_id?: string;
  waiting_for_approval?: boolean;
  filled_by?: string;
  to_arrange?: string[];
  reason?: string;
  desk_options?: { way: string; summary: string; cost_inr: number }[];
  note?: string;
  proposal?: { way: string; summary: string; proposed_by: string; approved_by?: string | null };
  report_id?: string;
};

export type Plan = {
  plan_id: string;
  status: string;
  trip_id: string;
  seat: string;
  replacing: string;
  crew_id: string;
  name: string;
  summary: string;
  cost_inr: number;
  trip_version: number;
  submitted_by: string;
  submitted_at: string;
  source: string;
  note_from_submitter?: string;
  decided_by?: string;
  decided_at?: string | null;
  decision_note?: string;
  to_arrange?: string[];
  report_id?: string;
};

export type Notice = { to: string; subject: string; body: string; at: string; ref?: string };

export type Choice = {
  position: number;
  way: string;
  summary: string;
  cost_inr: number;
  crew_id?: string;
  name?: string;
  rank?: string;
  base?: string;
  fatigue?: { severity: string; detail: string }[];
  to_arrange?: string[];
  departure_late_hours?: number;
  risk?: Risk;
  reason?: string;
  cost_breakdown?: Record<string, number>;
  minutes_to_airport?: number;
  note?: string;
};

export type CoverResult = {
  trip_id: string;
  seat: string;
  choices: Choice[];
  more_choices: number;
  considered: number;
  funnel: { stage: string; left: number; dropped: number; reason: string }[];
  facts: Record<string, number>;
  picks: Record<string, { crew_id: string; summary: string; cost_inr: number }>;
  why_not?: Record<string, string | { stage: string; rules: string[]; reasons: string[] }>;
  withdrawn?: Choice[];
};

export type TripDetails = {
  trip_id: string;
  airline: string;
  tail: string;
  days: string[];
  report: string;
  release: string;
  route: string;
  version: number;
  legs: { flight: string; from: string; to: string; departs: string; arrives: string; booked: number; seats: number }[];
  crew: { crew_id: string; name: string; seat: string }[];
};

export type TripFlight = { leg_id: string; flight_no: string; from: string; to: string; departs: string; tail: string; gate?: string | null; cancelled?: boolean };

export type Trip = {
  trip_id: string;
  airline: string;
  tail: string;
  cancelled?: boolean;
  flights?: TripFlight[];
  days: string[];
  report: string;
  release: string;
  route: string;
};

export type Unavailability = {
  report_id: string;
  from: string;
  to: string;
  reason: string;
  kind: string;
  since?: string;
  reported_by: string;
  reported_at: string;
};

export type Profile = {
  crew_id: string;
  name: string;
  rank: string;
  airline: string;
  base: string;
  ratings: string[];
  status: string;
  certificates: { kind: string; expires: string; days_left: number }[];
  duty_hours_last_7_days: number;
  duty_limit_7_days: number;
  block_hours_last_28_days: number;
  block_limit_28_days: number;
  rest_until: string | null;
  upcoming_duties: { trip_id: string; day: string; report: string }[];
  unavailable: Unavailability[];
  standby_days: string[];
  risk?: Risk;
};

export type DelayResult = {
  trip_id: string;
  day: string;
  delay_hours: number;
  crew_over_a_limit: { crew_id: string; name: string; rank?: string; rules: string[]; details?: string[] }[];
  crew_still_legal?: string[];
  crew_illegal_even_on_time?: string[];
  passengers: number;
  delay_cost_inr: number;
};

export type GateMove = {
  flight: string;
  leg_id: string;
  trip_id?: string;
  from_gate: string;
  to_gate: string;
  to: string;
  boarding_from: string;
  departs: string;
  passengers: number;
  delay_minutes: number;
  other_free_gates?: string[];
};

export type GatePlan = {
  station: string;
  what: string;
  moves: GateMove[];
  flights_moved: number;
  passengers_moved: number;
  crew_impact: DelayResult[];
};

export type GateChangeResult = {
  flight: string;
  leg_id: string;
  station: string;
  current_gate: string;
  wanted_gate: string;
  free: boolean;
  held_by?: string[];
  free_gates?: string[];
};

export type HoldResult = {
  what: string;
  flights_held: {
    flight: string;
    leg_id: string;
    route: string;
    planned: string;
    leaves: string;
    late_minutes: number;
    passengers: number;
  }[];
  passengers: number;
  trips: DelayResult[];
  crew_over_a_limit: number;
};

export type GateStatus = {
  station: string;
  gates: { gate: string; kind: string }[];
  departures: {
    flight: string;
    leg_id: string;
    to: string;
    gate: string;
    boarding_from: string;
    departs: string;
    passengers: number;
  }[];
};

// -- disruption reports -----------------------------------------------------------------------

export type ReportStatus = "open" | "in_progress" | "resolved" | "closed" | "revoked";

export type DeskRef = { desk_id: string; name: string; controller: string };

export type ReportSummary = {
  report_id: string;
  kind: string;
  label?: string;
  title: string;
  status: ReportStatus;
  desk: DeskRef | null;
  filed_by: string;
  filed_at: string;
  severity: { level: Level; reasons?: string[] } | null;
  seats_open?: number;
  plans_waiting?: number;
  crew_ids?: string[];
  evidence?: { level: "high" | "medium" | "low" | "read" | "none"; label: string; photos?: number };
  resolution_note?: string;
  /** Flight changes the resolution makes when an approver confirms it. */
  changes?: string[];
};

export type EvidenceItem = { evidence_id: string; kind: string; message: string; confidence?: string | null; at: string; check?: string };

export type Seat = { trip_id: string; day: string; rank: string; replacing: string; name: string; reports_at: string; started?: boolean; choices?: Choice[] };
export type Fill = {
  trip_id: string;
  rank: string;
  replacing: string;
  crew_id: string;
  name: string;
  cost: number;
  summary: string;
  risk?: Risk;
  /** A plan or callout id when the seat is already being handled. */
  in_hand?: string;
};

export type CrewAnalysis = {
  crew: { crew_id: string; name: string; rank: string; back_on_duty?: boolean }[];
  seats: Seat[];
  plan: { fills: Fill[]; unfilled: { trip_id: string; rank: string; replacing: string; why: string }[]; total_cost: number };
  seats_open: number;
};

export type Team = {
  team: string;
  title: string;
  job: string;
  station: string | null;
  contact: { name: string; phone: string } | null;
};

export type Report = ReportSummary & {
  summary: string;
  filed_role: string;
  details: Record<string, unknown>;
  severity: { level: Level; reasons: string[] } | null;
  analysis: Record<string, unknown> | null;
  teams: Team[];
  plans: Plan[];
  callouts: Callout[];
  history: { at: string; what: string; by: string; note?: string }[];
  resolution_note?: string | null;
  changes?: string[];
  can: Partial<Record<"plan" | "close" | "confirm" | "return" | "revoke" | "reassign", boolean>>;
};

export type DeskLoad = {
  desk_id: string;
  name: string;
  controller: string;
  open: number;
  in_progress: number;
  resolved: number;
  closed: number;
  total: number;
};

export type QueueItem = { kind: string; ref: string; title: string; detail: string; link: string; tone: "critical" | "pending" | "info" };

export type Board = {
  clock: string;
  brief: { headline: string; lines: string[]; mood: "calm" | "busy" | "critical"; written_by?: string; image_key?: string | null } | null;
  issues: Issue[];
  callouts: Callout[];
  changes: { trip_id: string; version: number; at: string; by: string; reason: string; added: string[]; removed: string[] }[];
  next_24h?: {
    flights: number;
    passengers: number;
    crew_out: number;
    open_callouts: number;
    illegal: number;
    fatigue: number;
    certificates: number;
    plans_waiting?: number;
    escalated?: number;
  };
  desks?: { controller: string; open: number; filled: number; to_approve: number }[];
  departures?: { flight: string; route: string; departs: string; tail: string; trip_id?: string; booked: number; state: string }[];
  standby?: { airline: string; rank: string; on_call: number }[];
  latest?: { subject: string; at: string; tone: string }[];
};

export type SpeakerFeed = {
  clock: string;
  waiting: { plans: number; decisions: number; proposals?: number; resolutions?: number; disruptions?: number };
  summary?: string[];
  items: { id: string; at: string; to: string; tone: "critical" | "pending" | "ok" | "info"; subject: string; body: string }[];
};

export type Answer = {
  conversation_id: string;
  text: string;
  verified: boolean;
  problems: string[];
  draft: string | null;
  draft_problems: string[];
  blocked: string | null;
  route: { intent: string; by: string; effort: string; tools: string[] } | null;
  steps: { tool: string; arguments: unknown; result: unknown }[];
  tokens: { prompt: number; completion: number; reasoning: number; cached: number };
  calls: { model: string; purpose: string; effort?: string; prompt: number; completion: number; cached: number; seconds: number }[];
};
