import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router";
import { ToastProvider } from "./data/Toast";
import { routes } from "./routes";
import PairGate from "./shell/PairGate";
import { readTheme, setTheme } from "./shell/theme";
import "./styles.css";

// Applies the stored theme and stores the light default, so index.html's pre-paint agrees next time.
setTheme(readTheme());

const queryClient = new QueryClient();
const router = createBrowserRouter(routes);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <PairGate>
          <RouterProvider router={router} />
        </PairGate>
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
);
