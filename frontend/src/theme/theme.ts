import { createTheme } from "@mui/material";

export const theme = createTheme({
  palette: {
    mode: "light",
    primary: {
      main: "#235789"
    },
    secondary: {
      main: "#4f7d5a"
    },
    warning: {
      main: "#a45d16"
    },
    error: {
      main: "#b3261e"
    },
    background: {
      default: "#f7f8fa"
    }
  },
  typography: {
    fontFamily:
      '"Noto Sans TC", "Microsoft JhengHei", system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    h4: {
      fontWeight: 700,
      letterSpacing: 0
    },
    h6: {
      fontWeight: 700,
      letterSpacing: 0
    },
    button: {
      letterSpacing: 0
    }
  },
  shape: {
    borderRadius: 8
  }
});
