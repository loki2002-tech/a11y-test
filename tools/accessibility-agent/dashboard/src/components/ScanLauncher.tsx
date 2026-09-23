"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card";
import { Loader2 } from "lucide-react";

export function ScanLauncher() {
  const [url, setUrl] = useState("");
  const [mode, setMode] = useState("automated");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const router = useRouter();

  const handleScan = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!url) return;
    
    setIsLoading(true);
    setError("");
    
    try {
      const response = await api.startScan({
        url,
        mode,
        agentic: mode === "full",
        browser: "chromium",
        headless: true
      });
      
      router.push(`/scans/${response.run_id}`);
    } catch (err: any) {
      setError(err.message || "Failed to start scan");
      setIsLoading(false);
    }
  };

  return (
    <Card className="w-full max-w-2xl mx-auto">
      <CardHeader>
        <CardTitle>New Accessibility Scan</CardTitle>
        <CardDescription>Enter a URL to start an automated accessibility audit.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleScan} className="flex flex-col space-y-4">
          <div className="flex space-x-2">
            <Input 
              type="url" 
              placeholder="https://example.com" 
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              required
              className="flex-1"
            />
            <select 
              value={mode} 
              onChange={(e) => setMode(e.target.value)}
              className="flex h-10 items-center justify-between rounded-md border border-gray-200 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-600 focus:ring-offset-2"
            >
              <option value="automated">Automated Scan</option>
              <option value="full">Full Agentic Scan</option>
            </select>
            <Button type="submit" disabled={isLoading || !url}>
              {isLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Start Scan
            </Button>
          </div>
          {error && <p className="text-sm text-red-500">{error}</p>}
        </form>
      </CardContent>
    </Card>
  );
}
