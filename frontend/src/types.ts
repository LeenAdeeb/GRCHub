export type Status = "pending_review" | "approved" | "rejected";

export interface Req {
  id: number;
  framework: string;
  version: string;
  control_id: string;
  printed_id: string;
  id_is_derived: boolean;
  kind: string;
  domain: string | null;
  subdomain_id: string | null;
  subdomain_title: string | null;
  principle: string | null;
  objective: string | null;
  original_text: string;
  page_index: number;
  page_label: string | null;
  extraction_confidence: number;
  parse_notes: string[];
  validation_status: Status;
  reviewed_by: string | null;
  reviewed_at: string | null;
}

export interface ReviewEntry {
  reviewer: string;
  decision: "approved" | "rejected";
  note: string | null;
  text_sha256: string;
  decided_at: string;
}

export interface ReqDetail extends Req {
  reviews: ReviewEntry[];
}

export interface Page {
  total: number;
  limit: number;
  offset: number;
  items: Req[];
}

export interface Stat {
  framework: string;
  version: string;
  status: Status;
  n: number;
}

export interface Subdomain {
  framework: string;
  subdomain_id: string;
  subdomain_title: string;
}

export interface Filters {
  framework: string;
  status: Status | "";
  subdomain: string;
  flagged: boolean;
  q: string;
}
