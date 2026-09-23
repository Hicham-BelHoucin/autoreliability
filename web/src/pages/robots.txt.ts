import type { APIRoute } from 'astro';

export const prerender = false;
export const GET: APIRoute = async ({ url }) => {
  const origin = (import.meta.env.SITE_URL ?? process.env.SITE_URL ?? url.origin).replace(/\/$/, '');
  return new Response(`User-agent: *\nAllow: /\nDisallow: /api/\nSitemap: ${origin}/sitemap.xml\n`, { headers: { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'public, max-age=3600' } });
};
