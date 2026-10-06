import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV === "development";

// Production: static export served by FastAPI on the same origin.
// Development (`npm run dev`): proxy /api/* to a backend running on :8000.
const nextConfig: NextConfig = isDev
  ? {
      async rewrites() {
        const backend = process.env.BACKEND_URL ?? "http://localhost:8000";
        return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
      },
    }
  : {
      output: "export",
      images: { unoptimized: true },
    };

export default nextConfig;
