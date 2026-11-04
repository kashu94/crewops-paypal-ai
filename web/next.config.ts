import type { NextConfig } from "next";

// The console is a static site: a plain export, served by the FastAPI server (on a laptop and
// when deployed), or hosted apart with the API's address in NEXT_PUBLIC_CREWPAY_API. `next dev` has
// no API of its own, so in development only, /api is proxied to a locally running backend
// (CREWPAY_API, default port 8000).
const dev = process.env.NODE_ENV === "development";
const backend = process.env.CREWPAY_API ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  trailingSlash: true,
  images: { unoptimized: true },
  devIndicators: false,
  // keep `next dev` from writing its own instruction files into the project
  agentRules: false,
  ...(dev
    ? {
        async rewrites() {
          return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
        },
      }
    : { output: "export" as const }),
};

export default nextConfig;
