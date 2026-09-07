/** Mirrors the Pydantic schemas in src/app/schemas/job.py. */

export type JobStatus = 'queued' | 'processing' | 'completed' | 'failed';

export interface JobCreated {
  job_id: string;
  status: JobStatus;
}

export interface JobProgress {
  job_id: string;
  status: JobStatus;
  title: string | null;
  total_resumes: number;
  processed_resumes: number;
  failed_resumes: number;
  /** processed / total, 0..1 */
  progress: number;
  error: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface FailedResume {
  filename: string;
  error: string;
}

export interface CandidateResult {
  candidate_id: string;
  name: string | null;
  filename: string;
  rank: number;
  /** All three are 0-100. */
  final_score: number;
  skill_score: number;
  semantic_score: number;
  matched_skills: string[];
  missing_skills: string[];
  education: Record<string, unknown>[];
  experience: Record<string, unknown>[];
}

export interface JobResults {
  job_id: string;
  status: JobStatus;
  run_id: string;
  jd_text: string;
  required_skills: string[];
  w_skill: number;
  w_semantic: number;
  total_resumes: number;
  processed_resumes: number;
  failed_resumes: number;
  failures: FailedResume[];
  results: CandidateResult[];
}
