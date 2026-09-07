interface Props {
  skills: string[];
  variant: 'matched' | 'missing';
  /** Collapse long lists behind a "+N more" toggle. */
  limit?: number;
}

import { useState } from 'react';

export function SkillChips({ skills, variant, limit = 6 }: Props) {
  const [expanded, setExpanded] = useState(false);

  if (skills.length === 0) {
    return <span className="muted small">—</span>;
  }

  const shown = expanded ? skills : skills.slice(0, limit);
  const hidden = skills.length - shown.length;

  return (
    <span className="chips">
      {shown.map((skill) => (
        <span key={skill} className={`chip chip--${variant}`}>
          {skill}
        </span>
      ))}
      {hidden > 0 && (
        <button type="button" className="link-button small" onClick={() => setExpanded(true)}>
          +{hidden} more
        </button>
      )}
      {expanded && skills.length > limit && (
        <button type="button" className="link-button small" onClick={() => setExpanded(false)}>
          show less
        </button>
      )}
    </span>
  );
}
