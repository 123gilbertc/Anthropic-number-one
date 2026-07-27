import type { Metadata } from "next";
import { Fraunces, Manrope } from "next/font/google";
import "./globals.css";

const display = Fraunces({
  subsets: ["latin"],
  variable: "--font-display",
});

const sans = Manrope({
  subsets: ["latin"],
  variable: "--font-sans",
});

export const metadata: Metadata = {
  title: "AngleCast — Multi-cam podcast studio in the browser",
  description:
    "Record like Google Meet. Walk away with multi-cam podcast footage. One camera. Infinite angles. Perfect backgrounds.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${display.variable} ${sans.variable}`}>
      <body className="min-h-full bg-ink-900 text-mist-100 antialiased">{children}</body>
    </html>
  );
}
