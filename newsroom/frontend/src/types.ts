export type SessionState =
  | "IDLE"
  | "BRIEFING"
  | "LIVE"
  | "WRAPPING"
  | "REVIEW"
  | "DONE"
  | "FAILED";

export type ConversationMessage = {
  id: string;
  role: "host" | "guest";
  text: string;
  stageDirection?: string;
  timestamp: string;
  typing?: boolean;
};

export type DirectorHint = {
  id: string;
  text: string;
  urgency: 1 | 2 | 3;
  type?: string;
  timestamp: string;
  source?: string;
};

export type SessionSnapshot = {
  id: string;
  scenario_id: string;
  persona_id: string;
  student_id: string;
  state: SessionState;
  topic: string;
  surface_bio: string;
  persona_name: string;
  facts_total: number;
  duration_seconds: number;
  briefing_seconds: number;
  report_id: string | null;
  turn_count: number;
  error_message: string | null;
};

export type ScenarioPreview = {
  scenario_id: string;
  topic: string;
  surface_bio: string;
  persona_id: string;
  persona_name: string;
  facts_total: number;
  created_at: string | null;
};

export type GuestDonePayload = {
  action: "reveal" | "partial" | "tell" | "deflect";
  targeted_fact: string | null;
  speech: string;
  stage_direction: string;
  request_id?: string | null;
  turn_idx?: number;
};

export type DimensionScore = {
  name: string;
  score: number;
  max: number;
};

export type ReplayRound = {
  round: number;
  timestamp: string;
  host: string;
  guest: string;
  stageDirection?: string;
  director?: string;
  studentAction: string;
  followed: boolean | null;
};

export type DossierFact = {
  id: string;
  content: string;
  juiciness: number;
  status: "found" | "missed";
  unlockHint: string;
  sources?: Array<{ url: string; quote: string }>;
};

export type ObjectiveMetric = {
  name: string;
  value: string;
  ideal: string;
  inRange: boolean;
};

export type SessionComparison = {
  name: string;
  current: number;
  previous: number;
  delta: number;
  direction: "up" | "down" | "same";
};

export type ReviewData = {
  id: string;
  topic: string;
  personaName: string;
  total: number;
  duration: string;
  dimensions: DimensionScore[];
  rounds: ReplayRound[];
  dossier: DossierFact[];
  metrics: ObjectiveMetric[];
  advice: string[];
  comparison: SessionComparison[];
};

export type SessionHistory = {
  turns: Array<{
    idx: number;
    role: "host" | "guest" | "director";
    content: string;
    meta_json: Record<string, unknown>;
    ts: string;
  }>;
  facts_found: number;
  found_fact_ids: string[];
};
