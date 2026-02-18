import { Toaster } from "@/components/ui/toaster";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import StitchScreens from "./pages/StitchScreens";

const queryClient = new QueryClient();
const routerBase =
  typeof window !== "undefined" && window.location.pathname.startsWith("/app")
    ? "/app"
    : "/";

const App = () => (
  <QueryClientProvider client={queryClient}>
    <TooltipProvider>
      <Toaster />
      <Sonner />
      <BrowserRouter basename={routerBase}>
        <Routes>
          <Route path="/" element={<StitchScreens />} />
          <Route path="/stitch" element={<StitchScreens />} />
          <Route path="*" element={<StitchScreens />} />
        </Routes>
      </BrowserRouter>
    </TooltipProvider>
  </QueryClientProvider>
);

export default App;
