import { Suspense } from "react";

import { SessionProvider } from "@/components/session";
import { Shell } from "@/components/shell";
import { Loading } from "@/components/ui";

export default function SignedInLayout({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider>
      <Shell>
        <Suspense fallback={<Loading />}>{children}</Suspense>
      </Shell>
    </SessionProvider>
  );
}
