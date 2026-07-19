import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "VC Brain — Investor Intelligence",
  description: "Evidence-backed venture intelligence from first signal to investment decision.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
