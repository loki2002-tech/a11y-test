import Link from "next/link";
import { ScanLauncher } from "@/components/ScanLauncher";

export default function Home() {
  return (
    <main className="container mx-auto p-4 flex flex-col items-center justify-center min-h-[80vh]">
      <div className="text-center mb-10">
        <h1 className="text-4xl font-extrabold tracking-tight mb-4">
          Autonomous Accessibility Testing
        </h1>
        <p className="text-lg text-gray-600 max-w-2xl mx-auto">
          Scan web applications for WCAG violations and automatically generate PRs with code fixes.
        </p>
      </div>
      
      <ScanLauncher />
    </main>
  );
}
