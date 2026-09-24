import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Droplets,
  Eraser,
  LoaderCircle,
  SendHorizontal,
  Sparkles,
  Sprout,
} from "lucide-react";
import { toast } from "sonner";
import { api, type ChatMessage, type SuggestedAction, type Zone } from "@/lib/api";

type Bubble = {
  id: string;
  role: "user" | "assistant";
  content: string;
  degraded?: boolean;
  action?: SuggestedAction | null;
};

const STARTERS = [
  "Does this zone need watering today?",
  "Why was the last cycle skipped?",
  "Which crop suits this soil right now?",
  "The leaves are turning yellow — what could it be?",
];

/**
 * AI advisory chat for a single zone. Every answer is grounded in that zone's
 * live snapshot server-side, so the zone selector matters — switching zones
 * loads a different conversation.
 */
export default function AIChat({
  zones,
  zoneId,
  onZoneChange,
  onPumpStarted,
}: {
  zones: Zone[];
  zoneId: string | null;
  onZoneChange: (zoneId: string) => void;
  onPumpStarted?: () => void;
}) {
  const [messages, setMessages] = useState<Bubble[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Load the stored conversation whenever the selected zone changes.
  useEffect(() => {
    if (!zoneId) {
      setMessages([]);
      return;
    }
    let cancelled = false;
    setLoadingHistory(true);
    api
      .chatHistory(zoneId)
      .then((rows: ChatMessage[]) => {
        if (cancelled) return;
        setMessages(
          rows
            .filter((row) => row.role === "user" || row.role === "assistant")
            .map((row) => ({ id: row.id, role: row.role as "user" | "assistant", content: row.content })),
        );
      })
      .catch(() => {
        if (!cancelled) setMessages([]);
      })
      .finally(() => {
        if (!cancelled) setLoadingHistory(false);
      });
    return () => {
      cancelled = true;
    };
  }, [zoneId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, sending]);

  const send = async (text: string) => {
    const message = text.trim();
    if (!message || sending) return;
    if (!zoneId) {
      toast.error("Select a zone first", { description: "The assistant answers using that zone's data." });
      return;
    }

    const localId = `local-${Date.now()}`;
    setMessages((current) => [...current, { id: localId, role: "user", content: message }]);
    setDraft("");
    setSending(true);

    try {
      const response = await api.chat(zoneId, message);
      setMessages((current) => [
        ...current,
        {
          id: `${localId}-reply`,
          role: "assistant",
          content: response.reply,
          degraded: response.degraded,
          action: response.suggested_action,
        },
      ]);
    } catch (error) {
      const detail = error instanceof Error ? error.message : "The assistant is unavailable.";
      setMessages((current) => [
        ...current,
        { id: `${localId}-error`, role: "assistant", content: detail, degraded: true },
      ]);
      toast.error("Assistant unavailable", { description: detail });
    } finally {
      setSending(false);
    }
  };

  // The assistant never runs the pump itself — it only proposes. This is the
  // farmer's explicit confirmation, which is what actually calls the backend.
  const confirmAction = async (action: SuggestedAction) => {
    setConfirming(true);
    try {
      await api.controlPump(action.zone_id, "start", action.duration_seconds);
      toast.success("Watering queued", {
        description: `${action.duration_seconds}s (~${action.liters.toFixed(2)} L). The node runs it on its next poll.`,
      });
      setMessages((current) =>
        current.map((bubble) => (bubble.action === action ? { ...bubble, action: null } : bubble)),
      );
      onPumpStarted?.();
    } catch (error) {
      toast.error("Could not start the pump", {
        description: error instanceof Error ? error.message : "Try again from the Overview panel.",
      });
    } finally {
      setConfirming(false);
    }
  };

  const clear = async () => {
    if (!zoneId) return;
    try {
      await api.clearChatHistory(zoneId);
      setMessages([]);
      toast.success("Conversation cleared");
    } catch (error) {
      toast.error("Could not clear the conversation", {
        description: error instanceof Error ? error.message : undefined,
      });
    }
  };

  const activeZone = zones.find((zone) => zone.id === zoneId);

  return (
    <section className="panel chat-panel">
      <div className="panel-heading">
        <div>
          <div className="section-eyebrow">Ask the field</div>
          <h2>AI advisory</h2>
        </div>
        <div className="chat-head-actions">
          <select
            className="chat-zone-select"
            value={zoneId ?? ""}
            onChange={(event) => onZoneChange(event.target.value)}
            aria-label="Zone for the assistant"
          >
            {zones.length === 0 && <option value="">No zones yet</option>}
            {zones.map((zone) => (
              <option key={zone.id} value={zone.id}>
                {zone.name}
              </option>
            ))}
          </select>
          <button className="icon-button plain" onClick={clear} title="Clear conversation" aria-label="Clear conversation">
            <Eraser size={16} />
          </button>
        </div>
      </div>

      <div className="chat-context">
        <Sprout size={13} />
        <span>
          Answers use live data from <strong>{activeZone?.name ?? "—"}</strong>
          {activeZone ? ` · ${activeZone.soil_type} soil · target ${activeZone.moisture_threshold_low}–${activeZone.moisture_threshold_high}%` : ""}
        </span>
      </div>

      <div className="chat-scroll" ref={scrollRef}>
        {loadingHistory && (
          <div className="chat-empty">
            <LoaderCircle size={18} className="spin" />
            <p>Loading conversation…</p>
          </div>
        )}

        {!loadingHistory && messages.length === 0 && (
          <div className="chat-empty">
            <div className="chat-empty-icon">
              <Sparkles size={20} />
            </div>
            <strong>Ask about watering, crops or plant problems.</strong>
            <p>
              The assistant reads this zone's live sensors before answering, so it can tell you what is
              actually happening — not a generic guess.
            </p>
          </div>
        )}

        {messages.map((bubble) => (
          <div className={`chat-row ${bubble.role}`} key={bubble.id}>
            {bubble.role === "assistant" && (
              <div className="chat-avatar">
                <Sparkles size={14} />
              </div>
            )}
            <div className="chat-bubble">
              <p>{bubble.content}</p>

              {bubble.degraded && (
                <div className="chat-degraded">
                  <AlertTriangle size={12} /> Offline answer from the rule-based advisor — the language model
                  was unreachable.
                </div>
              )}

              {bubble.action && (
                <div className="chat-action">
                  <div>
                    <strong>{bubble.action.label}</strong>
                    <small>
                      {bubble.action.duration_seconds}s · ~{bubble.action.liters.toFixed(2)} L estimated
                    </small>
                  </div>
                  <button onClick={() => confirmAction(bubble.action!)} disabled={confirming}>
                    {confirming ? <LoaderCircle size={14} className="spin" /> : <Droplets size={14} />}
                    Confirm
                  </button>
                </div>
              )}
            </div>
          </div>
        ))}

        {sending && (
          <div className="chat-row assistant">
            <div className="chat-avatar">
              <Sparkles size={14} />
            </div>
            <div className="chat-bubble typing">
              <i />
              <i />
              <i />
            </div>
          </div>
        )}
      </div>

      {messages.length === 0 && !loadingHistory && (
        <div className="chat-starters">
          {STARTERS.map((starter) => (
            <button key={starter} onClick={() => send(starter)} disabled={!zoneId}>
              {starter}
            </button>
          ))}
        </div>
      )}

      <div className="chat-composer">
        <textarea
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              send(draft);
            }
          }}
          rows={1}
          maxLength={1000}
          placeholder={zoneId ? "Ask about this zone…" : "Add a zone to start asking"}
          disabled={!zoneId || sending}
        />
        <button onClick={() => send(draft)} disabled={!zoneId || sending || !draft.trim()} aria-label="Send">
          {sending ? <LoaderCircle size={16} className="spin" /> : <SendHorizontal size={16} />}
        </button>
      </div>
    </section>
  );
}
