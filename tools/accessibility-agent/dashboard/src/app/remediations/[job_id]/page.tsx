"use client";

import React, { use } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Loader2, AlertCircle, CheckCircle2, GitPullRequest } from "lucide-react";
import ReactDiffViewer from "react-diff-viewer-continued";

export default function RemediationDetailPage({ params }: { params: Promise<{ job_id: string }> }) {
  const unwrappedParams = use(params);
  const { data: job, error } = useSWR(
    `remediation-${unwrappedParams.job_id}`,
    () => api.getRemediationStatus(unwrappedParams.job_id),
    { 
      refreshInterval: (data) => 
        (data?.status === 'completed' || data?.status === 'failed') ? 0 : 2000 
    }
  );

  if (error) return <div className="p-8 text-red-500">Error loading job status.</div>;
  if (!job) return <div className="p-8 flex items-center"><Loader2 className="animate-spin mr-2"/> Loading job...</div>;

  const isRunning = job.status === "pending" || job.status === "running";
  const isFailed = job.status === "failed";
  const isCompleted = job.status === "completed";

  return (
    <div className="container mx-auto p-4 space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold mb-1">Remediation Job</h1>
          <p className="text-sm text-gray-500 font-mono">{job.job_id}</p>
        </div>
        <Badge variant={
          isRunning ? "warning" : 
          isFailed ? "destructive" : 
          "success"
        } className="text-sm px-3 py-1">
          {job.status.toUpperCase()}
        </Badge>
      </div>

      {isRunning && (
        <Card>
          <CardContent className="pt-6 flex flex-col items-center justify-center space-y-4">
            <Loader2 className="h-12 w-12 animate-spin text-blue-500" />
            <p className="text-lg font-medium">{job.progress || "Agent is working..."}</p>
          </CardContent>
        </Card>
      )}

      {isFailed && (
        <Card className="border-red-200 bg-red-50">
          <CardContent className="pt-6 flex items-start space-x-3 text-red-700">
            <AlertCircle className="h-6 w-6 shrink-0 mt-0.5" />
            <div>
              <h3 className="font-semibold text-lg">Job Failed</h3>
              <p>{job.error}</p>
            </div>
          </CardContent>
        </Card>
      )}

      {job.summary?.pr_url && (
        <Card className="border-green-200 bg-green-50">
          <CardContent className="pt-6 flex items-start space-x-3 text-green-800">
            <GitPullRequest className="h-6 w-6 shrink-0 mt-0.5" />
            <div>
              <h3 className="font-semibold text-lg mb-1">Pull Request Created Successfully</h3>
              <a 
                href={job.summary.pr_url} 
                target="_blank" 
                rel="noreferrer"
                className="text-green-700 underline hover:text-green-900 font-medium"
              >
                {job.summary.pr_url}
              </a>
            </div>
          </CardContent>
        </Card>
      )}

      {job.finding_results && job.finding_results.length > 0 && (
        <div className="space-y-6 mt-8">
          <h2 className="text-2xl font-semibold">Applied Patches</h2>
          {job.finding_results.map((result: any, i: number) => (
            <Card key={i} className="overflow-hidden">
              <div className="border-b bg-gray-50 p-4">
                <div className="flex items-center space-x-2 mb-2">
                  <Badge variant={result.status === "VERIFIED" ? "success" : "warning"}>
                    {result.status}
                  </Badge>
                  <span className="font-semibold text-gray-700">{result.finding_id}</span>
                </div>
                {result.error && <p className="text-sm text-red-600 mt-2">{result.error}</p>}
              </div>
              
              {result.patch?.diff && (
                <div className="text-sm">
                  <ReactDiffViewer 
                    oldValue={result.patch.original_content || ""} 
                    newValue={result.patch.new_content || ""} 
                    splitView={false} 
                    hideLineNumbers={false}
                  />
                </div>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
