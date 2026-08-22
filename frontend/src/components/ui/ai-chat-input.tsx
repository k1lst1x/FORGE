import { ArrowUp, Mic, Paperclip, Sparkles, StopCircle } from "lucide-react";
import { useCallback, useRef, useState } from "react";

export type PromptInputMeta = {
  model: string;
  effort: string;
  attachments: File[];
};

type PromptInputProps = {
  placeholder?: string;
  onSubmit: (message: string, meta: PromptInputMeta) => void;
};

const MODELS = ["GPT 5.5", "Gemini 3.5 Flash", "Composer 2.5"];
const EFFORTS = ["Low", "Medium", "Max Effort"];

export function PromptInput({ placeholder = "Ask anything...", onSubmit }: PromptInputProps) {
  const [message, setMessage] = useState("");
  const [model, setModel] = useState(MODELS[0]);
  const [effort, setEffort] = useState(EFFORTS[1]);
  const [attachments, setAttachments] = useState<File[]>([]);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const submit = useCallback(() => {
    const trimmed = message.trim();
    if (!trimmed) return;

    onSubmit(trimmed, { model, effort, attachments });
    setMessage("");
    setAttachments([]);
  }, [attachments, effort, message, model, onSubmit]);

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <div className="prompt-shell">
      <div className="prompt-toolbar">
        <button
          type="button"
          className="prompt-icon-button"
          onClick={() => fileInputRef.current?.click()}
          aria-label="Attach files"
        >
          <Paperclip size={14} />
          Attach
        </button>

        <select
          className="prompt-select"
          value={model}
          onChange={(event) => setModel(event.target.value)}
          aria-label="Select model"
        >
          {MODELS.map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </select>

        <select
          className="prompt-select"
          value={effort}
          onChange={(event) => setEffort(event.target.value)}
          aria-label="Select effort"
        >
          {EFFORTS.map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </select>

        <input
          ref={fileInputRef}
          type="file"
          multiple
          hidden
          onChange={(event) => {
            const nextFiles = Array.from(event.target.files ?? []);
            setAttachments((current) => [...current, ...nextFiles]);
            event.target.value = "";
          }}
        />
      </div>

      <textarea
        className="prompt-input"
        rows={3}
        value={message}
        onChange={(event) => setMessage(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
      />

      {attachments.length > 0 ? (
        <div className="prompt-attachments" aria-live="polite">
          {attachments.map((file, index) => (
            <span key={`${file.name}-${index}`} className="prompt-attachment-pill">
              {file.name}
              <button
                type="button"
                className="prompt-attachment-remove"
                aria-label={`Remove ${file.name}`}
                onClick={() => setAttachments((current) => current.filter((_, i) => i !== index))}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      ) : null}

      <div className="prompt-footer">
        <div className="prompt-side-actions">
          <button type="button" className="prompt-ghost subtle" aria-label="Voice input">
            <Mic size={14} />
          </button>
          <span className="prompt-hint">
            <Sparkles size={12} />
            Press Enter to send
          </span>
        </div>

        <button type="button" className="prompt-send-button" onClick={submit} disabled={!message.trim()}>
          <ArrowUp size={15} />
          Send
        </button>
      </div>
    </div>
  );
}

export function PromptInputStopButton() {
  return (
    <button type="button" className="prompt-icon-button stop" aria-label="Stop generating">
      <StopCircle size={14} />
    </button>
  );
}
