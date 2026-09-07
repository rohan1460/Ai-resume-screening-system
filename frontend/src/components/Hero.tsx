import { HeroScene } from './HeroScene';

/**
 * Hero background: résumé sheets drifting behind the type.
 *
 * Pure CSS rather than the WebGL scene the mock shipped with — a hero should sit
 * behind the words, and three.js would add ~600KB to a page whose job is a table.
 * Each sheet carries its own distance, tilt and timing as custom properties, so one
 * keyframe animates the whole field.
 */

interface Sheet {
  left: string;
  top: string;
  width: number;
  /** Farther sheets are smaller, fainter and slower, which reads as depth. */
  depth: 'far' | 'mid' | 'near';
  driftFrom: string;
  driftTo: string;
  tiltFrom: string;
  tiltTo: string;
  duration: string;
  delay: string;
}

const SHEETS: Sheet[] = [
  {
    left: '6%',
    top: '18%',
    width: 38,
    depth: 'mid',
    driftFrom: '0px',
    driftTo: '-22px',
    tiltFrom: '-12deg',
    tiltTo: '4deg',
    duration: '7s',
    delay: '0s',
  },
  {
    left: '14%',
    top: '60%',
    width: 27,
    depth: 'far',
    driftFrom: '-8px',
    driftTo: '14px',
    tiltFrom: '18deg',
    tiltTo: '-6deg',
    duration: '9s',
    delay: '-2s',
  },
  {
    left: '2%',
    top: '74%',
    width: 48,
    depth: 'near',
    driftFrom: '6px',
    driftTo: '-16px',
    tiltFrom: '8deg',
    tiltTo: '-14deg',
    duration: '6.5s',
    delay: '-4s',
  },
  {
    left: '88%',
    top: '14%',
    width: 32,
    depth: 'far',
    driftFrom: '4px',
    driftTo: '-18px',
    tiltFrom: '-20deg',
    tiltTo: '2deg',
    duration: '8.5s',
    delay: '-1s',
  },
  {
    left: '81%',
    top: '48%',
    width: 50,
    depth: 'near',
    driftFrom: '-10px',
    driftTo: '18px',
    tiltFrom: '10deg',
    tiltTo: '-10deg',
    duration: '7.5s',
    delay: '-3s',
  },
  {
    left: '93%',
    top: '70%',
    width: 28,
    depth: 'mid',
    driftFrom: '0px',
    driftTo: '-20px',
    tiltFrom: '-6deg',
    tiltTo: '16deg',
    duration: '10s',
    delay: '-5s',
  },
  {
    left: '71%',
    top: '8%',
    width: 22,
    depth: 'far',
    driftFrom: '-6px',
    driftTo: '12px',
    tiltFrom: '14deg',
    tiltTo: '-8deg',
    duration: '11s',
    delay: '-6s',
  },
  {
    left: '25%',
    top: '6%',
    width: 24,
    depth: 'far',
    driftFrom: '2px',
    driftTo: '-14px',
    tiltFrom: '-16deg',
    tiltTo: '6deg',
    duration: '9.5s',
    delay: '-7s',
  },
];

const DEPTH = {
  far: { opacity: 0.3, blur: '1.4px' },
  mid: { opacity: 0.45, blur: '0.5px' },
  near: { opacity: 0.6, blur: '0px' },
} as const;

function Sheets() {
  return (
    <div className="hero__sheets" aria-hidden>
      {SHEETS.map((sheet, index) => {
        const depth = DEPTH[sheet.depth];
        return (
          <span
            key={index}
            className="sheet"
            style={
              {
                left: sheet.left,
                top: sheet.top,
                width: sheet.width,
                // A4-ish proportions, so they read as pages.
                height: sheet.width * 1.35,
                opacity: depth.opacity,
                filter: `blur(${depth.blur})`,
                '--drift-from': sheet.driftFrom,
                '--drift-to': sheet.driftTo,
                '--tilt-from': sheet.tiltFrom,
                '--tilt-to': sheet.tiltTo,
                '--drift-duration': sheet.duration,
                '--drift-delay': sheet.delay,
              } as React.CSSProperties
            }
          />
        );
      })}
    </div>
  );
}

interface Props {
  /** The WebGL core is only worth mounting on the first screen. */
  scene?: boolean;
  badge: string;
  title: React.ReactNode;
  lede: string;
  children?: React.ReactNode;
}

export function Hero({ scene = false, badge, title, lede, children }: Props) {
  return (
    <section className="hero">
      {/* Self-contained washes: no external asset to go missing. */}
      <div className="hero__wash" aria-hidden />
      <div className="hero__grid" aria-hidden />
      {/* Sheets paint instantly; the WebGL core fades in over them once loaded. */}
      <Sheets />
      {scene && <HeroScene />}

      <div className="hero__body">
        <span className="rise hero__badge" style={{ animationDelay: '0ms' }}>
          <span className="dot" />
          {badge}
        </span>

        <h1 className="rise" style={{ animationDelay: '110ms' }}>
          {title}
        </h1>

        <p className="rise hero__lede" style={{ animationDelay: '220ms' }}>
          {lede}
        </p>

        {children && (
          <div className="rise" style={{ animationDelay: '330ms', marginTop: 22 }}>
            {children}
          </div>
        )}
      </div>
    </section>
  );
}
