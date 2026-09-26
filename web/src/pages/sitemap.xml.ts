import type { APIRoute } from 'astro';
import { pool } from '../lib/db';

export const prerender = false;
const xmlEscape = (value: string) => value.replace(/[<>&'\"]/g, (character) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', "'": '&apos;', '"': '&quot;' }[character] ?? character));

export const GET: APIRoute = async ({ url }) => {
  const origin = (import.meta.env.SITE_URL ?? process.env.SITE_URL ?? url.origin).replace(/\/$/, '');
  const staticRoutes = ['/', '/directory', '/rankings', '/compare', '/about', '/privacy', '/terms', '/data-license', '/contact', '/disclaimer'];
  const result = await pool.query('SELECT DISTINCT make, model, year, last_synced_at FROM vehicle_reliability WHERE total_complaints >= 10 ORDER BY make, model, year');
  const entries = [
    ...staticRoutes.map((path) => `<url><loc>${xmlEscape(`${origin}${path}`)}</loc><changefreq>weekly</changefreq><priority>${path === '/' ? '1.0' : '0.7'}</priority></url>`),
    ...result.rows.map((row) => {
      const path = `/reliability/${encodeURIComponent(row.make.toLowerCase())}/${encodeURIComponent(row.model.toLowerCase())}/${row.year}`;
      return `<url><loc>${xmlEscape(`${origin}${path}`)}</loc><lastmod>${new Date(row.last_synced_at).toISOString().slice(0, 10)}</lastmod><changefreq>weekly</changefreq><priority>0.8</priority></url>`;
    }),
  ];
  return new Response(`<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${entries.join('')}</urlset>`, { headers: { 'Content-Type': 'application/xml; charset=utf-8', 'Cache-Control': 'public, max-age=3600' } });
};
