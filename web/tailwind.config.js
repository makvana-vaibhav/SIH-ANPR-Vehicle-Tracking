/** @type {import('tailwindcss').Config} */

// Sentinel-GJ command centre theme.
//
// Dark by default: this is a wall-mounted operations display in a control room,
// not a document. The palette is shadcn/ui-compatible (HSL CSS variables in
// src/styles/index.css) so components drop in without restyling.
//
// Status colours are semantic and used consistently everywhere a camera, alert,
// or route hop is rendered — an operator learns them once.
export default {
  darkMode: ['class'],
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    container: {
      center: true,
      padding: '1.5rem',
      screens: { '2xl': '1600px' },
    },
    extend: {
      colors: {
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))',
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))',
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))',
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))',
        },
        popover: {
          DEFAULT: 'hsl(var(--popover))',
          foreground: 'hsl(var(--popover-foreground))',
        },
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))',
        },

        // ── Semantic status palette ──────────────────────────────────
        // Camera fleet state, alert priority, and route-hop confidence all
        // map onto these. Never hardcode a status colour in a component.
        status: {
          online: 'hsl(var(--status-online))',
          offline: 'hsl(var(--status-offline))',
          degraded: 'hsl(var(--status-degraded))',
          unknown: 'hsl(var(--status-unknown))',
        },
        priority: {
          critical: 'hsl(var(--priority-critical))',
          high: 'hsl(var(--priority-high))',
          medium: 'hsl(var(--priority-medium))',
          low: 'hsl(var(--priority-low))',
        },
      },
      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
      },
      fontFamily: {
        // Plates, timestamps, camera codes and coordinates are all read
        // character-by-character — they belong in a monospace face.
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        mono: ['JetBrains Mono', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      keyframes: {
        'pulse-alert': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.45' },
        },
        'ticker-in': {
          from: { opacity: '0', transform: 'translateY(-6px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: {
        // Critical alerts pulse until acknowledged.
        'pulse-alert': 'pulse-alert 1.4s ease-in-out infinite',
        'ticker-in': 'ticker-in 220ms ease-out',
      },
    },
  },
  plugins: [],
}
