import type { Metadata, Viewport } from "next";
import "./globals.css";
import Providers from "./providers";

export const metadata: Metadata = {
  title: "ShowUp · by Neovia AI",
  description: "WhatsApp appointment reminders for clinics",
};

/** `cover` lets the layout use the iPhone notch/home-bar areas (we pad them with env(safe-area-inset-*)). */
export const viewport: Viewport = { width: "device-width", initialScale: 1, viewportFit: "cover" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
