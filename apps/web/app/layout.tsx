import "@xyflow/react/dist/style.css";
import "./globals.css";

import { AppShell } from "@/components/shell/app-shell";

export const metadata = {
  title: "No More 500s",
  description: "Agent execution observability",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}

