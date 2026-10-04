import { useEffect } from "react";
import { Shell } from "./components/Shell";
import { useRoute } from "./router";
import { connectStream, useStore } from "./store";
import { AuthScreen, Settings } from "./screens/Account";
import { GameWorkspace } from "./screens/GameWorkspace";
import { LiveBoard } from "./screens/LiveBoard";
import { Performance } from "./screens/Performance";
import { Portfolio } from "./screens/Portfolio";
import { Admin, Research } from "./screens/Research";

export function App() {
  const route = useRoute();
  useEffect(() => { connectStream(); }, []);
  const errors = useStore((s) => s.errors);
  const healthErr = errors.health;
  useEffect(() => { window.scrollTo(0, 0); }, [route.name, (route as any).id]);
  return (
    <Shell route={route}>
      {healthErr && <div className="error" role="alert" style={{ marginBottom: 16 }}>Server unavailable: {healthErr}</div>}
      {route.name === "board" && <LiveBoard />}
      {route.name === "watchlist" && <LiveBoard onlyWatchlist />}
      {route.name === "game" && <GameWorkspace id={route.id} />}
      {route.name === "portfolio" && <Portfolio />}
      {route.name === "performance" && <Performance />}
      {route.name === "research" && <Research tab={route.tab} />}
      {route.name === "settings" && <Settings />}
      {route.name === "admin" && <Admin tab={route.tab} />}
      {route.name === "login" && <AuthScreen mode="login" />}
      {route.name === "signup" && <AuthScreen mode="signup" />}
    </Shell>
  );
}
