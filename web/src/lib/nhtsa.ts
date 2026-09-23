export interface NhtsaComplaintReport {
  odiNumber: string;
  dateComplaintFiled: string | null;
  dateOfIncident: string | null;
  components: string[];
  summary: string | null;
  crash: boolean;
  fire: boolean;
}

const endpoint = 'https://api.nhtsa.gov/complaints/complaintsByVehicle';

function stringOrNull(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function asBoolean(value: unknown): boolean {
  return value === true || value === 'true' || value === 1 || value === '1';
}

function mapComplaint(value: unknown): NhtsaComplaintReport | null {
  if (!value || typeof value !== 'object') return null;
  const row = value as Record<string, unknown>;
  const odiNumber = row.odiNumber;
  if (typeof odiNumber !== 'string' && typeof odiNumber !== 'number') return null;

  const components = Array.isArray(row.components)
    ? row.components.filter((component): component is string => typeof component === 'string' && Boolean(component.trim()))
    : typeof row.components === 'string'
      ? row.components.split(',').map((component) => component.trim()).filter(Boolean)
      : [];

  return {
    odiNumber: String(odiNumber),
    dateComplaintFiled: stringOrNull(row.dateComplaintFiled),
    dateOfIncident: stringOrNull(row.dateOfIncident),
    components,
    summary: stringOrNull(row.summary),
    crash: asBoolean(row.crash),
    fire: asBoolean(row.fire),
  };
}

export async function getNhtsaComplaintReports(make: string, model: string, year: number, limit = 50): Promise<NhtsaComplaintReport[]> {
  const url = new URL(endpoint);
  url.search = new URLSearchParams({ make, model, modelYear: String(year) }).toString();
  const response = await fetch(url, { signal: AbortSignal.timeout(10_000) });
  if (!response.ok) throw new Error(`NHTSA complaints API responded with ${response.status}`);

  const payload: unknown = await response.json();
  if (!payload || typeof payload !== 'object' || !Array.isArray((payload as Record<string, unknown>).results)) return [];
  return (payload as { results: unknown[] }).results.map(mapComplaint).filter((report): report is NhtsaComplaintReport => report !== null).slice(0, Math.min(Math.max(limit, 1), 100));
}

export function nhtsaComplaintSourceUrl(make: string, model: string, year: number): string {
  const url = new URL(endpoint);
  url.search = new URLSearchParams({ make, model, modelYear: String(year) }).toString();
  return url.toString();
}
