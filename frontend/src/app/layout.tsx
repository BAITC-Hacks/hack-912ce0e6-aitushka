import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Граф денег — Aitushka",
  description: "Локальное рабочее пространство для исследования сети переводов и объяснимых приоритетов проверки.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ru"><body>{children}</body></html>;
}
