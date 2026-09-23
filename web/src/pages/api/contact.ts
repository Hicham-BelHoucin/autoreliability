import type { APIRoute } from 'astro';

export const prerender = false;

const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const responseHeaders = { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' };
const lastSubmissionByIp = new Map<string, number>();

const escapeHtml = (value: string) => value.replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character] ?? character));

export const POST: APIRoute = async ({ request, url }) => {
  const origin = request.headers.get('origin');
  if (origin && origin !== url.origin) {
    return new Response(JSON.stringify({ message: 'Invalid request origin.' }), { status: 403, headers: responseHeaders });
  }
  const clientIp = request.headers.get('x-forwarded-for')?.split(',')[0]?.trim() ?? 'unknown';
  const now = Date.now();
  const previousSubmission = lastSubmissionByIp.get(clientIp);
  if (previousSubmission && now - previousSubmission < 60_000) {
    return new Response(JSON.stringify({ message: 'Please wait one minute before sending another message.' }), { status: 429, headers: responseHeaders });
  }

  let payload: { name?: unknown; email?: unknown; message?: unknown; website?: unknown };
  try {
    payload = await request.json();
  } catch {
    return new Response(JSON.stringify({ message: 'Please submit the form again.' }), { status: 400, headers: responseHeaders });
  }

  const name = typeof payload.name === 'string' ? payload.name.trim() : '';
  const email = typeof payload.email === 'string' ? payload.email.trim() : '';
  const message = typeof payload.message === 'string' ? payload.message.trim() : '';
  const website = typeof payload.website === 'string' ? payload.website.trim() : '';
  if (website) return new Response(JSON.stringify({ message: 'Thanks for your message.' }), { status: 200, headers: responseHeaders });
  if (name.length < 2 || name.length > 120 || !emailPattern.test(email) || message.length < 20 || message.length > 5_000) {
    return new Response(JSON.stringify({ message: 'Enter your name, a valid email address, and a message between 20 and 5,000 characters.' }), { status: 400, headers: responseHeaders });
  }

  const apiKey = import.meta.env.RESEND_API_KEY ?? process.env.RESEND_API_KEY;
  const from = import.meta.env.CONTACT_FROM_EMAIL ?? process.env.CONTACT_FROM_EMAIL;
  const to = import.meta.env.CONTACT_TO_EMAIL ?? process.env.CONTACT_TO_EMAIL;
  if (!apiKey || !from || !to) {
    console.error('Contact email is not configured.');
    return new Response(JSON.stringify({ message: 'The contact form is temporarily unavailable. Please email us directly.' }), { status: 503, headers: responseHeaders });
  }

  try {
    const delivery = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: { Authorization: `Bearer ${apiKey}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        from,
        to: [to],
        reply_to: email,
        subject: `Website contact: ${name}`,
        text: `Name: ${name}\nEmail: ${email}\n\n${message}`,
        html: `<p><strong>Name:</strong> ${escapeHtml(name)}<br><strong>Email:</strong> ${escapeHtml(email)}</p><p>${escapeHtml(message).replace(/\n/g, '<br>')}</p>`,
      }),
    });
    if (!delivery.ok) {
      console.error('Contact email delivery failed.', delivery.status);
      return new Response(JSON.stringify({ message: 'We could not send your message. Please try again or email us directly.' }), { status: 502, headers: responseHeaders });
    }
  } catch (error) {
    console.error('Contact email service error.', error);
    return new Response(JSON.stringify({ message: 'We could not send your message. Please try again or email us directly.' }), { status: 502, headers: responseHeaders });
  }

  lastSubmissionByIp.set(clientIp, now);
  return new Response(JSON.stringify({ message: 'Thanks—your message has been sent.' }), { status: 200, headers: responseHeaders });
};
