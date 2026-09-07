import { useMemo, useState } from 'react';
import type { CandidateResult } from '../types';
import { SkillChips } from './SkillChips';

type SortKey = 'rank' | 'name' | 'final_score' | 'skill_score' | 'semantic_score';
type Direction = 'asc' | 'desc';

interface Props {
  results: CandidateResult[];
  weights: { skill: number; semantic: number };
}

function compare(a: CandidateResult, b: CandidateResult, key: SortKey): number {
  if (key === 'name') {
    return (a.name ?? a.filename).localeCompare(b.name ?? b.filename);
  }
  return a[key] - b[key];
}

export function ResultsTable({ results, weights }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>('rank');
  const [direction, setDirection] = useState<Direction>('asc');

  const sorted = useMemo(() => {
    const copy = [...results];
    copy.sort((a, b) => {
      const result = compare(a, b, sortKey);
      return direction === 'asc' ? result : -result;
    });
    return copy;
  }, [results, sortKey, direction]);

  function toggle(key: SortKey) {
    if (key === sortKey) {
      setDirection((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      // Scores read best highest-first; rank and name read best lowest-first.
      setDirection(key === 'rank' || key === 'name' ? 'asc' : 'desc');
    }
  }

  function header(key: SortKey, label: string, className?: string) {
    const active = key === sortKey;
    return (
      <th
        className={className}
        aria-sort={active ? (direction === 'asc' ? 'ascending' : 'descending') : 'none'}
      >
        <button type="button" className="sort-button" onClick={() => toggle(key)}>
          {label}
          <span className={`caret${active ? ' caret--active' : ''}`}>
            {active ? (direction === 'asc' ? '▲' : '▼') : '▽'}
          </span>
        </button>
      </th>
    );
  }

  if (results.length === 0) {
    return <p className="muted">No candidates were scored — every resume failed to parse.</p>;
  }

  return (
    <div className="table-scroll">
      <table className="results">
        <thead>
          <tr>
            {header('rank', '#', 'col-rank')}
            {header('name', 'Candidate', 'col-candidate')}
            {header('final_score', 'Final', 'col-score')}
            {header('skill_score', `Skill ×${weights.skill}`, 'col-score')}
            {header('semantic_score', `Semantic ×${weights.semantic}`, 'col-score')}
            <th>Matched skills</th>
            <th>Missing skills</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <tr key={row.candidate_id}>
              <td className="col-rank">{row.rank}</td>
              <td className="col-candidate">
                <div className="candidate-name">{row.name ?? '(name not found)'}</div>
                <div className="muted small mono">{row.filename}</div>
              </td>
              <td className="col-score">
                <div className="score-final">{row.final_score.toFixed(1)}</div>
                <div className="score-meter">
                  <div className="score-meter__fill" style={{ width: `${row.final_score}%` }} />
                </div>
              </td>
              <td className="col-score muted">{row.skill_score.toFixed(1)}</td>
              <td className="col-score muted">{row.semantic_score.toFixed(1)}</td>
              <td>
                <SkillChips skills={row.matched_skills} variant="matched" />
              </td>
              <td>
                <SkillChips skills={row.missing_skills} variant="missing" />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
