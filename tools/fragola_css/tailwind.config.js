module.exports = {
  content: ["./markup.html"],
  important: ".fa-scope",
  corePlugins: { preflight: false },
  theme: {
    extend: {
      colors: {
        "brand-purple": "#844B8C",
        "brand-purple-dark": "#6B3A72",
        "brand-cyan": "#00D9FF",
        "brand-dark": "#0a0a0f",
        "brand-surface": "#151520",
      },
      fontFamily: { mono: ['"IBM Plex Mono"', "monospace"] },
    },
  },
};
