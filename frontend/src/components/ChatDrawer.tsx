"use client";

import { useEffect, useRef, useState } from "react";
import { X, Send, Sparkles, Loader2 } from "lucide-react";
import { api, type Candidate, type RowResult } from "@/lib/api";
import { cn, formatPercent, truncate } from "@/lib/utils";

type Msg =
  | { role: "user"; text: string }
  | {
      role: "assistant";
      text: string;
      candidates?: Candidate[];
      streaming?: boolean;
      contextRowIdx?: number;
    };

export function ChatDrawer({
  open,
  onClose,
  contextRow,
}: {
  open: boolean;
  onClose: () => void;
  contextRow: RowResult | null;
}) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Cancel any in-flight stream on unmount or close.
  useEffect(() => {
    if (!open && abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    return () => {
      if (abortRef.current) {
        abortRef.current.abort();
        abortRef.current = null;
      }
    };
  }, [open]);

  // When a row is set as context, auto-add a system intro message.
  useEffect(() => {
    if (open && contextRow) {
      setMsgs((prev) => {
        if (prev.some((m) => m.role === "assistant" && m.contextRowIdx === contextRow.row_idx)) {
          return prev;
        }
        return [
          ...prev,
          {
            role: "assistant",
            text: `Looking at row ${contextRow.row_idx + 1}: "${truncate(
              contextRow.item_description,
              80
            )}". Declared CTH ${contextRow.declared_cth || "—"} → recommended ${
              contextRow.final?.cth_final || "—"
            }. Ask anything.`,
            contextRowIdx: contextRow.row_idx,
          },
        ];
      });
    }
  }, [open, contextRow]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [msgs]);

  // Lock body scroll while open
  useEffect(() => {
    document.body.style.overflow = open ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [open]);

  const send = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    setInput("");
    setMsgs((m) => [...m, { role: "user", text: trimmed }]);
    setBusy(true);
    const placeholder: Msg = { role: "assistant", text: "", streaming: true };
    setMsgs((m) => [...m, placeholder]);

    let acc = "";
    let candidates: Candidate[] | undefined;

    if (abortRef.current) abortRef.current.abort();
    const ac = new AbortController();
    abortRef.current = ac;

    try {
      for await (const evt of api.chatStream(trimmed, contextRow ? {
        row_idx: contextRow.row_idx,
        cth: contextRow.final?.cth_final || contextRow.declared_cth,
        description: contextRow.item_description,
      } : undefined, ac.signal)) {
        if (evt.type === "delta") {
          acc += typeof evt.data === "string" ? evt.data : evt.data?.text ?? "";
          setMsgs((m) => {
            const next = [...m];
            const last = next[next.length - 1];
            if (last && last.role === "assistant") {
              next[next.length - 1] = { role: "assistant", text: acc, streaming: true, candidates };
            }
            return next;
          });
        } else if (evt.type === "candidates") {
          candidates = evt.data?.candidates ?? evt.data ?? [];
          setMsgs((m) => {
            const next = [...m];
            const last = next[next.length - 1];
            if (last && last.role === "assistant") {
              next[next.length - 1] = { ...last, candidates };
            }
            return next;
          });
        } else if (evt.type === "error") {
          acc += `\n\n[error] ${typeof evt.data === "string" ? evt.data : JSON.stringify(evt.data)}`;
        }
      }
    } catch (e: any) {
      if (e?.name === "AbortError" || ac.signal.aborted) {
        acc = acc || "(cancelled)";
      } else {
        acc = acc || `Chat failed. ${e?.message ?? ""}`;
      }
    } finally {
      if (abortRef.current === ac) abortRef.current = null;
      setMsgs((m) => {
        const next = [...m];
        const last = next[next.length - 1];
        if (last && last.role === "assistant") {
          next[next.length - 1] = { role: "assistant", text: acc || "(no response)", candidates, streaming: false };
        }
        return next;
      });
      setBusy(false);
    }
  };

  if (!open) return null;

  return (
    <>
      <div
        className={cn(
          "fixed inset-0 z-40 bg-ink/15 backdrop-blur-[2px] transition-opacity duration-200",
          open ? "opacity-100" : "opacity-0 pointer-events-none"
        )}
        onClick={onClose}
      />
      <aside
        className={cn(
          "fixed right-0 top-0 z-50 h-full w-full max-w-[440px] bg-surface border-l border-line shadow-lift",
          "flex flex-col transition-transform duration-300 ease-[cubic-bezier(.2,.8,.2,1)]",
          open ? "translate-x-0" : "translate-x-full"
        )}
      >
        <header className="flex items-center justify-between border-b border-line px-4 py-3">
          <div className="flex items-center gap-2">
            <span className="grid h-7 w-7 place-items-center rounded-md bg-ink text-white">
              <Sparkles className="h-3.5 w-3.5" />
            </span>
            <div>
              <div className="text-sm font-semibold leading-tight">Customs assistant</div>
              <div className="text-[11px] text-ink-muted">
                {contextRow ? `Row ${contextRow.row_idx + 1} context loaded` : "Classification help on demand"}
              </div>
            </div>
          </div>
          <button onClick={onClose} className="btn-ghost h-8 w-8 p-0">
            <X className="h-4 w-4" />
          </button>
        </header>

        <div ref={logRef} className="flex-1 overflow-auto px-4 py-4 space-y-3 bg-page/40">
          {msgs.length === 0 && (
            <div className="rounded-xl border border-line bg-surface p-4 text-sm text-ink-muted">
              Ask about a product description, candidate heading, or BCD ambiguity.
            </div>
          )}
          {msgs.map((m, i) => (
            <Bubble key={i} msg={m} />
          ))}
        </div>

        <div className="border-t border-line bg-surface p-3">
          {!contextRow && (
            <div className="mb-2 flex flex-wrap gap-1.5">
              {[
                "What is the likely CTH for synthetic rubber sheets?",
                "Compare headings for stainless steel fasteners.",
                "Which heading fits industrial control panels?",
              ].map((s) => (
                <button
                  key={s}
                  onClick={() => send(s)}
                  className="chip hover:border-line2 hover:text-ink"
                >
                  {truncate(s, 40)}
                </button>
              ))}
            </div>
          )}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void send(input);
            }}
            className="flex items-center gap-2"
          >
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask anything about classification…"
              className="input flex-1"
              disabled={busy}
            />
            <button type="submit" disabled={busy || !input.trim()} className="btn-primary h-9 w-9 p-0">
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
            </button>
          </form>
        </div>
      </aside>
    </>
  );
}

function Bubble({ msg }: { msg: Msg }) {
  if (msg.role === "user") {
    return (
      <div className="ml-10 rounded-2xl rounded-tr-sm bg-ink px-3.5 py-2.5 text-sm text-white shadow-soft">
        {msg.text}
      </div>
    );
  }
  return (
    <div className="mr-10 space-y-2">
      <div className="rounded-2xl rounded-tl-sm border border-line bg-surface px-3.5 py-2.5 text-sm leading-relaxed text-ink shadow-soft">
        {msg.text || (msg.streaming ? <span className="inline-flex items-center gap-1 text-ink-muted"><Loader2 className="h-3 w-3 animate-spin" /> thinking…</span> : "—")}
        {msg.streaming && msg.text && <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse bg-ink align-middle" />}
      </div>
      {msg.candidates && msg.candidates.length > 0 && (
        <div className="rounded-xl border border-line bg-page/60 p-2 space-y-1.5">
          <div className="px-1 text-[10px] font-medium uppercase tracking-wider text-ink-muted">
            Matching candidates
          </div>
          {msg.candidates.slice(0, 4).map((c) => (
            <div key={c.cth} className="rounded-md bg-surface px-2.5 py-1.5 text-[12px]">
              <div className="flex items-center justify-between">
                <span className="font-mono font-semibold">{c.cth}</span>
                <span className="text-ink-muted">BCD {formatPercent(c.bcd_effective ?? c.bcd_statutory)}</span>
              </div>
              <div className="mt-0.5 text-ink-muted">{truncate(c.description, 90)}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
