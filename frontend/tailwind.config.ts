import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--font-inter)", "system-ui", "sans-serif"],
        serif: ["var(--font-serif)", "Georgia", "serif"],
        mono: ["ui-monospace", "SFMono-Regular", "monospace"],
      },
      colors: {
        ink: {
          DEFAULT: "#191918",
          soft: "#37352f",
          muted: "#787774",
        },
        page: "#fbfbfa",
        surface: "#ffffff",
        line: "#ebebea",
        line2: "#e3e2e0",
        accent: {
          DEFAULT: "#2c6df3",
          soft: "#eaf1ff",
        },
        warn: "#d97706",
        danger: "#dc2626",
        success: "#16a34a",
      },
      boxShadow: {
        soft: "0 1px 2px rgba(15,15,15,0.04), 0 1px 1px rgba(15,15,15,0.03)",
        lift: "0 12px 32px -12px rgba(15,15,15,0.18), 0 2px 6px rgba(15,15,15,0.04)",
      },
      animation: {
        "fade-in": "fadeIn 240ms ease-out both",
        "slide-up": "slideUp 360ms cubic-bezier(.2,.8,.2,1) both",
        shimmer: "shimmer 1.6s linear infinite",
      },
      keyframes: {
        fadeIn: {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        slideUp: {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-400px 0" },
          "100%": { backgroundPosition: "400px 0" },
        },
      },
    },
  },
  plugins: [],
};

export default config;
