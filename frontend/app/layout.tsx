import type { Metadata } from "next";
import { Geist } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Academic Planning Engine",
  description:
    "Swarthmore semester planner — deterministic schedule generation from your Degree Works audit.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col bg-slate-50 text-slate-900">
        <header className="border-b border-slate-200 bg-white">
          <div className="mx-auto max-w-5xl px-4 py-3 flex items-center justify-between">
            <div>
              <span className="font-semibold text-slate-900 text-sm">Academic Planning Engine</span>
              <span className="ml-2 text-xs text-slate-400">Swarthmore semester planner</span>
            </div>
          </div>
        </header>
        <main className="flex-1">{children}</main>
        <footer className="border-t border-slate-200 bg-white py-4">
          <div className="mx-auto max-w-5xl px-4 text-center text-xs text-slate-400">
            Files are processed for this planning session and are not stored permanently.
          </div>
        </footer>
      </body>
    </html>
  );
}
