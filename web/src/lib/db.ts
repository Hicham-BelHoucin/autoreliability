import { Pool, type QueryResultRow } from 'pg';

export interface ComponentBreakdown {
  component: string;
  complaints: number;
  percentage: number;
}

export interface Vehicle {
  make: string;
  model: string;
  year: number;
  reliabilityScore: number;
  totalComplaints: number;
  crashReports: number;
  fireReports: number;
  primaryFailureComponent: string | null;
  componentBreakdown: ComponentBreakdown[];
  aiSummary: string | null;
  lastSyncedAt: Date;
}

export interface LeaderboardEntry {
  make: string;
  model: string;
  year: number;
  totalComplaints: number;
  crashReports: number;
  fireReports: number;
  reliabilityScore: number;
  primaryFailureComponent: string | null;
  rank: number;
}

export interface VehicleSearchResult {
  make: string;
  model: string;
  year: number;
  totalComplaints: number;
  primaryFailureComponent: string | null;
}

const pool = new Pool({
  connectionString: import.meta.env.DATABASE_URL ?? process.env.DATABASE_URL,
  max: 20,
  idleTimeoutMillis: 30_000,
  connectionTimeoutMillis: 5_000,
  maxUses: 10_000,
  ssl: process.env.DATABASE_SSL === 'true' ? { rejectUnauthorized: false } : undefined,
});

pool.on('error', (error) => console.error('Unexpected PostgreSQL pool error', error));

function mapVehicle(row: QueryResultRow): Vehicle {
  return {
    make: row.make, model: row.model, year: Number(row.year), reliabilityScore: Number(row.reliability_score),
    totalComplaints: Number(row.total_complaints), crashReports: Number(row.crash_reports), fireReports: Number(row.fire_reports),
    primaryFailureComponent: row.primary_failure_component, componentBreakdown: row.component_breakdown ?? [],
    aiSummary: row.ai_summary, lastSyncedAt: new Date(row.last_synced_at),
  };
}

export async function getVehicle(make: string, model: string, year: number): Promise<Vehicle | null> {
  const result = await pool.query(
    `SELECT make, model, year, reliability_score, total_complaints, crash_reports, fire_reports,
            primary_failure_component, component_breakdown, ai_summary, last_synced_at
       FROM vehicle_reliability WHERE make = $1 AND model = $2 AND year = $3`,
    [make.toUpperCase(), model.toUpperCase(), year],
  );
  return result.rows[0] ? mapVehicle(result.rows[0]) : null;
}

export async function getLeaderboard(limit = 50): Promise<LeaderboardEntry[]> {
  const result = await pool.query(
    `SELECT make, model, year, total_complaints, crash_reports, fire_reports, reliability_score,
            primary_failure_component, rank
       FROM mv_top_unreliable_vehicles ORDER BY rank LIMIT $1`,
    [Math.min(Math.max(Math.floor(limit), 1), 100)],
  );
  return result.rows.map((row) => ({
    make: row.make, model: row.model, year: Number(row.year), totalComplaints: Number(row.total_complaints),
    crashReports: Number(row.crash_reports), fireReports: Number(row.fire_reports),
    reliabilityScore: Number(row.reliability_score), primaryFailureComponent: row.primary_failure_component, rank: Number(row.rank),
  }));
}

export async function getAvailableMakes(): Promise<string[]> {
  const result = await pool.query('SELECT DISTINCT make FROM vehicle_reliability ORDER BY make');
  return result.rows.map((row) => row.make as string);
}

export async function searchVehicles(query: string, limit = 60): Promise<VehicleSearchResult[]> {
  const normalized = query.trim();
  if (!normalized) return [];
  const result = await pool.query(
    `SELECT make, model, year, total_complaints, primary_failure_component
       FROM vehicle_reliability
      WHERE make ILIKE $1 OR model ILIKE $1 OR CAST(year AS TEXT) = $2
      ORDER BY total_complaints DESC, make, model, year
      LIMIT $3`,
    [`%${normalized}%`, normalized, Math.min(Math.max(Math.floor(limit), 1), 100)],
  );
  return result.rows.map((row) => ({
    make: row.make as string, model: row.model as string, year: Number(row.year),
    totalComplaints: Number(row.total_complaints), primaryFailureComponent: row.primary_failure_component as string | null,
  }));
}

export async function getAdjacentYears(make: string, model: string, year: number): Promise<Vehicle[]> {
  const result = await pool.query(
    `SELECT make, model, year, reliability_score, total_complaints, crash_reports, fire_reports,
            primary_failure_component, component_breakdown, ai_summary, last_synced_at
       FROM vehicle_reliability
      WHERE make = $1 AND model = $2 AND year IN ($3, $4)
      ORDER BY year`,
    [make.toUpperCase(), model.toUpperCase(), year - 1, year + 1],
  );
  return result.rows.map(mapVehicle);
}

export { pool };
