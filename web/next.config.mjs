/** The browser only ever talks to /api/*, which Next proxies to FastAPI: no CORS, no API URL in the bundle. */
const API_URL = process.env.API_URL || "http://127.0.0.1:8000";
/** Sites allowed to embed the public booking form in an <iframe>. Default: any (it is a public form with no login).
 *  Set NEXT_PUBLIC_EMBED_ORIGINS="https://smilecare.example https://www.smilecare.example" to restrict it. */
const EMBED = (process.env.NEXT_PUBLIC_EMBED_ORIGINS || "").trim();

/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/:path*` }];
  },
  async headers() {
    return [
      // Only the booking form may be framed. Everything else (the staff app, login) must not be, or it could be
      // clickjacked.
      { source: "/book/embed", headers: [{ key: "Content-Security-Policy", value: `frame-ancestors ${EMBED ? `'self' ${EMBED}` : "*"}` }] },
      { source: "/((?!book/embed).*)", headers: [{ key: "Content-Security-Policy", value: "frame-ancestors 'self'" }, { key: "X-Content-Type-Options", value: "nosniff" }] },
    ];
  },
};

export default nextConfig;
