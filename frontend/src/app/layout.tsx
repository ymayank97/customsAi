import type { Metadata } from "next";
import { Inter, Instrument_Serif } from "next/font/google";
import "./globals.css";
import { TopBar } from "@/components/TopBar";
import { AppProviders } from "@/providers/AppProviders";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

const serif = Instrument_Serif({
  subsets: ["latin"],
  weight: "400",
  variable: "--font-serif",
  display: "swap",
});

export const metadata: Metadata = {
  title: "customsAI — CTH fraud audit",
  description:
    "Upload a Bill of Entry. Audit declared CTH, BCD, and revenue risk in one pass.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${inter.variable} ${serif.variable}`}>
      <body className="min-h-screen flex flex-col">
        <AppProviders>
          <TopBar />
          <main className="flex-1 page-enter">{children}</main>
        </AppProviders>
      </body>
    </html>
  );
}
