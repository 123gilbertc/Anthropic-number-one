/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#07090C",
          900: "#0B0F14",
          800: "#121821",
          700: "#1A2330",
          600: "#243041",
        },
        brass: {
          300: "#F0C56D",
          400: "#E8A838",
          500: "#D4922A",
        },
        mist: {
          100: "#E8EEF5",
          300: "#A8B6C8",
          500: "#6B7C90",
        },
        signal: {
          live: "#E24B4A",
          ok: "#3DCF8E",
        },
      },
      fontFamily: {
        display: ["var(--font-display)", "system-ui", "sans-serif"],
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
      },
      backgroundImage: {
        "studio-glow":
          "radial-gradient(ellipse 80% 50% at 50% -20%, rgba(232,168,56,0.18), transparent), radial-gradient(ellipse 60% 40% at 100% 50%, rgba(61,207,142,0.06), transparent)",
        "grain":
          "url(\"data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.05'/%3E%3C/svg%3E\")",
      },
      boxShadow: {
        panel: "0 0 0 1px rgba(255,255,255,0.06), 0 24px 48px rgba(0,0,0,0.45)",
      },
    },
  },
  plugins: [],
};
