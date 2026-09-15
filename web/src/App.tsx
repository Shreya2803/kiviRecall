import { useState } from "react";
import { cn } from "@/lib/utils";
import DictationFeed from "@/components/DictationFeed";
import HeyKivi from "@/components/HeyKivi";
import WhatKiviKnows from "@/components/WhatKiviKnows";

const SCREENS = [
  { id: "feed", label: "Dictation feed", Component: DictationFeed },
  { id: "kivi", label: "Hey Kivi", Component: HeyKivi },
  { id: "knows", label: "What Kivi knows", Component: WhatKiviKnows },
] as const;

export default function App() {
  const [active, setActive] = useState<(typeof SCREENS)[number]["id"]>("feed");
  const Screen = SCREENS.find((s) => s.id === active)?.Component ?? DictationFeed;

  return (
    <div className="min-h-screen">
      <header className="border-b border-border">
        <div className="mx-auto flex max-w-2xl items-center gap-1 px-6 pt-4">
          <span className="mr-4 text-sm font-semibold">Kivi</span>
          {SCREENS.map((s) => (
            <button
              key={s.id}
              onClick={() => setActive(s.id)}
              className={cn(
                "rounded-t-md px-3 py-2 text-sm",
                active === s.id
                  ? "border-b-2 border-primary font-medium text-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {s.label}
            </button>
          ))}
        </div>
      </header>
      <main>
        <Screen />
      </main>
    </div>
  );
}
