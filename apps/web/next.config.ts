import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  transpilePackages: [
    "@anglecast/shared",
    "@anglecast/virtual-background",
    "@anglecast/recording",
    "@anglecast/angles",
  ],
  reactStrictMode: true,
};

export default nextConfig;
