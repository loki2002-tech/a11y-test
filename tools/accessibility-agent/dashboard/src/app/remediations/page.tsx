"use client";

import useSWR from "swr";
import { api } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import Link from "next/link";
import { formatDistanceToNow } from "date-fns";

export default function RemediationsPage() {
  const { data, error } = useSWR("remediations", api.listRemediations, { refreshInterval: 5000 });

  if (error) return <div className="p-8 text-red-500">Failed to load remediations.</div>;
  if (!data) return <div className="p-8">Loading...</div>;

  return (
    <div className="container mx-auto p-4 space-y-6">
      <h1 className="text-3xl font-bold mb-6">Remediation Jobs</h1>
      
      {data.length === 0 ? (
        <Card>
          <CardContent className="p-8 text-center text-gray-500">
            No remediation jobs found. Start one from the scan findings page.
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4">
          {data.map((job: any) => (
            <Link href={`/remediations/${job.job_id}`} key={job.job_id}>
              <Card className="hover:border-blue-300 transition-colors cursor-pointer">
                <CardContent className="p-4 flex items-center justify-between">
                  <div>
                    <div className="flex items-center space-x-3 mb-1">
                      <span className="font-mono text-sm text-gray-500">{job.job_id}</span>
                      <Badge variant={
                        job.status === "completed" ? "success" :
                        job.status === "failed" ? "destructive" :
                        "warning"
                      }>
                        {job.status.toUpperCase()}
                      </Badge>
                    </div>
                    <p className="text-sm font-medium">{job.progress}</p>
                  </div>
                  {job.created_at && (
                    <div className="text-sm text-gray-500 text-right">
                      Started {formatDistanceToNow(new Date(job.created_at))} ago
                    </div>
                  )}
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
