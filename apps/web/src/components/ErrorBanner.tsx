interface ErrorBannerProps {
  message: string;
  onRetry: (() => void) | null;
  onEdit: (() => void) | null;
  onCancel: (() => void) | null;
  onCheckpoint: (() => void) | null;
  onDismiss: () => void;
  busy: boolean;
}

const MAX_LENGTH = 400;

function tidy(message: string): string {
  const single = message.replace(/\s+/g, " ").trim();
  if (/traceback|at\s+\w+\s+\(|file\s+"[A-Za-z]:\\|\.py",\s*line\s+\d+|\{\"|\"\":/i.test(single)) {
    return "The server reported an unexpected failure. Retry, edit the request, or return to a checkpoint.";
  }
  return single.length > MAX_LENGTH ? `${single.slice(0, MAX_LENGTH)}…` : single || "The operation failed.";
}

export default function ErrorBanner({ message, onRetry, onEdit, onCancel, onCheckpoint, onDismiss, busy }: ErrorBannerProps) {
  return (
    <div className="error-banner global-error" role="alert">
      <span className="error-message">{tidy(message)}</span>
      <div className="error-actions">
        {onRetry && (
          <button className="text-button" onClick={onRetry} disabled={busy}>
            Retry
          </button>
        )}
        {onEdit && (
          <button className="text-button" onClick={onEdit} disabled={busy}>
            Edit
          </button>
        )}
        {onCancel && (
          <button className="text-button" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
        )}
        {onCheckpoint && (
          <button className="text-button" onClick={onCheckpoint} disabled={busy}>
            Return to checkpoint
          </button>
        )}
        <button className="text-button" onClick={onDismiss} disabled={busy}>
          Dismiss
        </button>
      </div>
    </div>
  );
}
