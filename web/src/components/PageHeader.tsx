import type { ReactNode } from "react";

export default function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <header className="mb-4 md:mb-6 flex flex-wrap items-end justify-between gap-3 empty:hidden">
      <h1 className="sr-only md:not-sr-only text-xl font-semibold tracking-tight">{title}</h1>
      {children}
    </header>
  );
}

export function Page({ children, narrow = false }: { children: ReactNode; narrow?: boolean }) {
  return <div className={`mx-auto w-full px-4 sm:px-6 lg:px-8 py-6 md:py-10 ${narrow ? "max-w-prompt" : "max-w-page"}`}>{children}</div>;
}
