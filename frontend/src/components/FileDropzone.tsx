import { useCallback, useRef, useState } from 'react';

const ACCEPTED = ['.pdf', '.docx'];

function isAccepted(file: File): boolean {
  const name = file.name.toLowerCase();
  return ACCEPTED.some((ext) => name.endsWith(ext));
}

interface Props {
  files: File[];
  onChange: (files: File[]) => void;
  disabled?: boolean;
}

export function FileDropzone({ files, onChange, disabled }: Props) {
  const [dragging, setDragging] = useState(false);
  const [rejected, setRejected] = useState<string[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  const add = useCallback(
    (incoming: FileList | null) => {
      if (!incoming) return;
      const all = Array.from(incoming);
      const accepted = all.filter(isAccepted);
      setRejected(all.filter((f) => !isAccepted(f)).map((f) => f.name));
      // De-duplicate by name + size so dropping the same batch twice is harmless.
      const seen = new Set(files.map((f) => `${f.name}:${f.size}`));
      const merged = [...files];
      for (const file of accepted) {
        const key = `${file.name}:${file.size}`;
        if (!seen.has(key)) {
          seen.add(key);
          merged.push(file);
        }
      }
      onChange(merged);
    },
    [files, onChange],
  );

  return (
    <div>
      <div
        className={`dropzone${dragging ? ' dropzone--active' : ''}${disabled ? ' dropzone--disabled' : ''}`}
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          if (!disabled) add(e.dataTransfer.files);
        }}
        onClick={() => !disabled && inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') inputRef.current?.click();
        }}
      >
        <strong>Drop resumes here</strong>
        <span className="muted">or click to browse — PDF and DOCX</span>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept=".pdf,.docx"
          hidden
          onChange={(e) => {
            add(e.target.files);
            e.target.value = '';
          }}
        />
      </div>

      {rejected.length > 0 && (
        <p className="error-text">Skipped (unsupported type): {rejected.join(', ')}</p>
      )}

      {files.length > 0 && (
        <ul className="file-list">
          {files.map((file) => (
            <li key={`${file.name}:${file.size}`}>
              <span className="file-name">{file.name}</span>
              <span className="muted">{(file.size / 1024).toFixed(0)} KB</span>
              <button
                type="button"
                className="link-button"
                disabled={disabled}
                onClick={() => onChange(files.filter((f) => f !== file))}
                aria-label={`Remove ${file.name}`}
              >
                remove
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
