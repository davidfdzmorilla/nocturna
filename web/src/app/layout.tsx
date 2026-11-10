import type { Metadata } from "next";
import "./globals.css";
import { SiteFooter } from "@/components/SiteFooter";
import { SiteHeader } from "@/components/SiteHeader";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: {
    default: "Nocturna",
    template: "%s · Nocturna",
  },
  description:
    "Nocturna. Resumen semanal del NASA Exoplanet Archive y hallazgos generados automáticamente a partir de arXiv astro-ph.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="es">
      <body className="flex min-h-screen flex-col bg-bg font-sans text-text antialiased">
        <a
          href="#contenido"
          className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-[100] focus:rounded-md focus:border focus:border-border focus:bg-surface focus:px-4 focus:py-2 focus:text-text"
        >
          Saltar al contenido
        </a>
        <SiteHeader />
        <main
          id="contenido"
          className="mx-auto w-full max-w-5xl flex-1 px-4 py-8 sm:px-6 sm:py-10"
        >
          {children}
        </main>
        <SiteFooter />
      </body>
    </html>
  );
}
