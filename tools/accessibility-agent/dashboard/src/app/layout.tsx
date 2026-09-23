import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import Link from "next/link";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "A11y Agent Dashboard",
  description: "Autonomous accessibility testing and remediation",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${inter.className} min-h-screen bg-gray-50 flex flex-col`}>
        <header className="border-b bg-white shrink-0">
          <div className="container mx-auto px-4 h-16 flex items-center justify-between">
            <Link href="/" className="flex items-center space-x-2">
              <span className="text-2xl">♿</span>
              <span className="font-bold text-xl">A11y Agent</span>
            </Link>
            <nav className="flex items-center space-x-6">
              <Link href="/" className="text-sm font-medium hover:text-blue-600">
                New Scan
              </Link>
              <Link href="/remediations" className="text-sm font-medium hover:text-blue-600">
                Remediations
              </Link>
            </nav>
          </div>
        </header>
        <div className="flex-1">
          {children}
        </div>
      </body>
    </html>
  );
}
