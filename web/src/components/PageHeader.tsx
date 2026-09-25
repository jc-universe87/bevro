import type { ReactNode } from "react";

/** A page's name, one light line about it, and at most a couple of quiet actions. */
export default function PageHeader({ title, lead, children }: { title: string; lead?: ReactNode; children?: ReactNode }) {
  return (
    <header className="mb-6 md:mb-8 flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      <div className="min-w-0">
        <h1 className="bv-title">{title}</h1>
        {lead && <p className="bv-lead mt-1">{lead}</p>}
      </div>
      {children}
    </header>
  );
}

export function Page({ children, narrow = false }: { children: ReactNode; narrow?: boolean }) {
  return <div className={`mx-auto w-full px-4 sm:px-6 lg:px-8 py-6 md:py-10 ${narrow ? "max-w-prompt" : "max-w-page"}`}>{children}</div>;
}
