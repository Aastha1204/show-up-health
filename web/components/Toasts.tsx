"use client";

import { createContext, useCallback, useContext, useRef, useState } from "react";

type Kind = "info" | "success" | "error";
type Toast = { id: number; text: string; kind: Kind };
const Ctx = createContext<(text: string, kind?: Kind) => void>(() => {});
export const useToast = () => useContext(Ctx);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const seq = useRef(0);
  const push = useCallback((text: string, kind: Kind = "info") => {
    const id = ++seq.current;
    setItems((l) => [...l.slice(-3), { id, text, kind }]);
    setTimeout(() => setItems((l) => l.filter((t) => t.id !== id)), kind === "error" ? 8000 : 5500);
  }, []);
  return (
    <Ctx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-[min(92vw,360px)] flex-col gap-2" aria-live="polite" role="status">
        {items.map((t) => (
          <div key={t.id} className={`toast-in pointer-events-auto rounded-lg border px-3.5 py-2.5 text-sm shadow-lg ${
            t.kind === "error" ? "border-red-400/60 bg-red-950 text-red-50"
            : t.kind === "success" ? "border-teal-400/60 bg-teal-900 text-teal-50" : "border-[var(--line)] bg-neutral-900 text-white"}`}>
            {t.text}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
