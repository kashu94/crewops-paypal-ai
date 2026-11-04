import type { Metadata, Viewport } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";

import "./globals.css";

const sans = IBM_Plex_Sans({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-plex-sans" });
const mono = IBM_Plex_Mono({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-plex-mono" });

export const metadata: Metadata = {
  title: { default: "CrewPay", template: "%s · CrewPay" },
  description: "Crew control for three airlines: legal cover, disruptions and approvals.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#eff3f7" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`h-full antialiased ${sans.variable} ${mono.variable}`}>
      <body className="flex min-h-full flex-col">{children}</body>
    </html>
  );
}
