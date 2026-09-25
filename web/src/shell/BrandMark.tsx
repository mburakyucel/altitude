/** The Altitude mark (SPEC §3.1): an A whose left side climbs in three steps (L1, L2, L3) to the
 * summit, on an accent tile. Decorative; the "Altitude" text beside it names the product.
 * `public/altitude-mark.svg` (favicon, README) and `public/apple-touch-icon.png` draw the same mark. */
export function BrandMark({ size }: { size: number }) {
  return (
    <svg className="brand-mark" aria-hidden viewBox="0 0 32 32" width={size} height={size}>
      <rect width="32" height="32" rx="7" fill="var(--accent)" />
      <path
        d="M7.5 25.5V19.5H11V13.5H14.5L16.5 7L24.5 25.5"
        fill="none"
        stroke="var(--on-accent)"
        strokeWidth="3.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
