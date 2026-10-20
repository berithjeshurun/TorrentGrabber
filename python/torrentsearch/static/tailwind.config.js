/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./index.html", "./js/**/*.js"],
  theme: {
    extend: {
      colors: {
        ink: { 950: "#070a0d", 900: "#0c1117", 800: "#141b24", 700: "#1c2633" },
        signal: { DEFAULT: "#f0a33a", soft: "#f7c77a" },
        mint: { DEFAULT: "#3dffa8", soft: "#9affe0" },
        fog: { DEFAULT: "#9aa8b8", bright: "#e8eef5" },
      },
      fontFamily: {
        display: ["Syne", "system-ui", "sans-serif"],
        sans: ["IBM Plex Sans", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
