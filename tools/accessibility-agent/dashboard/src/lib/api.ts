const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

export interface ScanRequest {
  url: string;
  mode?: string;
  agentic?: boolean;
  browser?: string;
  headless?: boolean;
  viewport?: string;
  auth_state_path?: string;
  login_url?: string;
  login_username?: string;
  login_password?: string;
}

export interface ScanResponse {
  run_id: string;
  message: string;
}

export interface ScanStatusResponse {
  run_id: string;
  status: string;
  progress: string;
  error?: string;
  report_paths?: Record<string, string>;
}

export interface RemediateRequest {
  finding: any;
  repo_path: string;
  dry_run?: boolean;
  no_tests?: boolean;
  no_pr?: boolean;
}

export interface RemediateResponse {
  job_id: string;
  status: string;
  message: string;
}

export interface RemediationStatusResponse {
  job_id: string;
  status: string;
  progress: string;
  error?: string;
  finding_results?: any[];
  summary?: any;
}

async function fetchJson<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${endpoint}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });
  
  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`API Error ${response.status}: ${errorText}`);
  }
  
  return response.json();
}

export const api = {
  startScan: (data: ScanRequest) => 
    fetchJson<ScanResponse>("/scan", {
      method: "POST",
      body: JSON.stringify(data),
    }),
    
  getScanStatus: (runId: string) => 
    fetchJson<ScanStatusResponse>(`/scans/${runId}/status`),
    
  getScanReport: (runId: string) => 
    fetchJson<any>(`/scans/${runId}/report`),
    
  startRemediation: (data: RemediateRequest) => 
    fetchJson<RemediateResponse>("/remediate", {
      method: "POST",
      body: JSON.stringify(data),
    }),
    
  getRemediationStatus: (jobId: string) => 
    fetchJson<RemediationStatusResponse>(`/remediations/${jobId}`),
    
  listRemediations: () => 
    fetchJson<RemediationStatusResponse[]>("/remediations"),
};
