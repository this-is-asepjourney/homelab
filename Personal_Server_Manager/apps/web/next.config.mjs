/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: 'standalone',
  productionBrowserSourceMaps: false,
  // Prevent Next.js from spawning extra 500-800MB TypeScript & ESLint child workers during Docker build on 4GB RAM servers
  eslint: {
    ignoreDuringBuilds: true,
  },
  typescript: {
    ignoreBuildErrors: true,
  },
  // Single-worker compilation cuts peak RAM from ~2.2 GB down to ~550 MB (eliminates Swap freeze)
  experimental: {
    workerThreads: false,
    cpus: 1,
  },
  async rewrites() {
    // Rewrites are resolved at build time; inside Docker the API is reachable as http://api:8000
    const apiUrl = process.env.INTERNAL_API_URL || 'http://api:8000';
    return [
      {
        source: '/api/v1/:path*',
        destination: `${apiUrl}/api/v1/:path*`,
      },
      {
        source: '/health',
        destination: `${apiUrl}/health`,
      },
      {
        source: '/install-agent.sh',
        destination: `${apiUrl}/install-agent.sh`,
      },
    ];
  },
};

export default nextConfig;
