"use client";

import React, { useEffect, useState, use } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Loader2, AlertCircle, CheckCircle2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useRouter } from "next/navigation";

export default function ScanDetailPage({ params }: { params: Promise<{ run_id: string }> }) {
  const router = useRouter();
  const unwrappedParams = use(params);
  
  // Poll status every 2 seconds while not completed or failed
  const { data: statusData, error: statusError } = useSWR(
    `status-${unwrappedParams.run_id}`,
    () => api.getScanStatus(unwrappedParams.run_id),
    { 
      refreshInterval: (data) => 
        (data?.status === 'completed' || data?.status === 'failed') ? 0 : 2000 
    }
  );

  // Fetch report only when completed
  const { data: reportData, error: reportError } = useSWR(
    statusData?.status === 'completed' ? `report-${unwrappedParams.run_id}` : null,
    () => api.getScanReport(unwrappedParams.run_id)
  );

  const handleRemediate = async (finding: any) => {
    try {
      const res = await api.startRemediation({
        finding: finding,
        repo_path: ".", // Path to the frontend project being scanned
        dry_run: false,
      });
      router.push(`/remediations/${res.job_id}`);
    } catch (err: any) {
      alert("Failed to start remediation: " + err.message);
    }
  };

  if (statusError) return <div className="p-8 text-red-500">Error loading scan status.</div>;
  if (!statusData) return <div className="p-8 flex items-center"><Loader2 className="animate-spin mr-2"/> Loading scan...</div>;

  const isRunning = statusData.status === "pending" || statusData.status === "running";
  const isFailed = statusData.status === "failed";

  return (
    <div className="container mx-auto p-4 space-y-6">
      <div className="flex justify-between items-center">
        <h1 className="text-3xl font-bold">Scan Results</h1>
        <Badge variant={
          isRunning ? "warning" : 
          isFailed ? "destructive" : 
          "success"
        } className="text-sm px-3 py-1">
          {statusData.status.toUpperCase()}
        </Badge>
      </div>

      {isRunning && (
        <Card>
          <CardContent className="pt-6 flex flex-col items-center justify-center space-y-4">
            <Loader2 className="h-12 w-12 animate-spin text-blue-500" />
            <p className="text-lg font-medium">{statusData.progress || "Scanning in progress..."}</p>
          </CardContent>
        </Card>
      )}

      {isFailed && (
        <Card className="border-red-200 bg-red-50">
          <CardContent className="pt-6 flex items-start space-x-3 text-red-700">
            <AlertCircle className="h-6 w-6 shrink-0 mt-0.5" />
            <div>
              <h3 className="font-semibold text-lg">Scan Failed</h3>
              <p>{statusData.error}</p>
            </div>
          </CardContent>
        </Card>
      )}

      {reportData && (
        <>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm text-gray-500 font-medium uppercase tracking-wider">Total Findings</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-4xl font-bold">{reportData.metrics.total_findings}</div>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm text-gray-500 font-medium uppercase tracking-wider">Axe Violations</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-4xl font-bold">{reportData.metrics.axe_violations}</div>
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm text-gray-500 font-medium uppercase tracking-wider">Target URL</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-lg truncate" title={reportData.url}>{reportData.url}</div>
              </CardContent>
            </Card>
          </div>

          <h2 className="text-2xl font-semibold mt-8 mb-4">Detailed Findings</h2>
          <div className="space-y-4">
            {reportData.findings.length === 0 ? (
              <Card>
                <CardContent className="pt-6 flex flex-col items-center text-green-600">
                  <CheckCircle2 className="h-12 w-12 mb-2" />
                  <p className="text-lg font-medium">No accessibility violations found!</p>
                </CardContent>
              </Card>
            ) : (
              reportData.findings.map((finding: any) => (
                <Card key={finding.finding_id} className="overflow-hidden">
                  <div className="border-b bg-gray-50/50 p-4 flex justify-between items-start">
                    <div>
                      <div className="flex items-center space-x-2 mb-1">
                        <Badge variant="destructive">{finding.rule_id}</Badge>
                        <Badge variant="outline">{finding.wcag.principle}</Badge>
                      </div>
                      <h3 className="font-semibold text-lg">{finding.description}</h3>
                    </div>
                    <Button onClick={() => handleRemediate(finding)}>
                      Auto-Remediate
                    </Button>
                  </div>
                  <CardContent className="p-4 space-y-4">
                    {finding.element.selector && (
                      <div>
                        <p className="text-sm font-medium text-gray-500 mb-1">Element Selector</p>
                        <code className="px-2 py-1 bg-gray-100 rounded text-sm break-all">
                          {finding.element.selector}
                        </code>
                      </div>
                    )}
                    {finding.element.html && (
                      <div>
                        <p className="text-sm font-medium text-gray-500 mb-1">HTML Source</p>
                        <pre className="p-3 bg-gray-900 text-gray-100 rounded text-sm overflow-x-auto">
                          {finding.element.html}
                        </pre>
                      </div>
                    )}
                    {finding.ai_reasoning?.explanation && (
                      <div className="bg-blue-50 border border-blue-100 rounded-md p-4">
                        <p className="text-sm font-semibold text-blue-900 mb-1">AI Analysis</p>
                        <p className="text-sm text-blue-800">{finding.ai_reasoning.explanation}</p>
                      </div>
                    )}
                  </CardContent>
                </Card>
              ))
            )}
          </div>
        </>
      )}
    </div>
  );
}
