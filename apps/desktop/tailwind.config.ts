import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"] ,
  theme: {
    extend: {
      colors: {
        midnight: {
          900: "#0f111a",
          800: "#151826",
          700: "#1b1f2f"
        },
        slate: {
          500: "#c1c4d6",
          700: "#8b90a8"
        },
        accent: {
          500: "#4f7cff"
        }
      }
    }
  },
  plugins: []
} satisfies Config;
