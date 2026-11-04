import { api, type DemoAccount } from "./api";

/** Used only when the server does not list its demo accounts. */
const FALLBACK: DemoAccount[] = [
  { user: "ananya.iyer", role: "controller", crew_id: null, label: "Ananya Iyer, desk 1" },
  { user: "rohit.malhotra", role: "controller", crew_id: null, label: "Rohit Malhotra, desk 2" },
  { user: "divya.rao", role: "controller", crew_id: null, label: "Divya Rao, desk 3" },
  { user: "meera.bhatt", role: "approver", crew_id: null, label: "Meera Bhatt" },
  { user: "karan.mehta", role: "approver", crew_id: null, label: "Karan Mehta" },
  { user: "farah.qureshi", role: "approver", crew_id: null, label: "Farah Qureshi" },
  { user: "imran.sheikh", role: "reporter", crew_id: null, label: "Imran Sheikh, HYD station duty manager" },
  { user: "lakshmi.hegde", role: "reporter", crew_id: null, label: "Lakshmi Hegde, HYD station duty manager" },
  { user: "nikhil.agarwal", role: "auditor", crew_id: null, label: "Nikhil Agarwal" },
  { user: "priya.desai", role: "admin", crew_id: null, label: "Priya Desai" },
  { user: "vikram.saxena", role: "super_admin", crew_id: null, label: "Vikram Saxena" },
  { user: "ops.speaker", role: "speaker", crew_id: null, label: "Ops-room speaker" },
  { user: "c1111.nair", role: "crew", crew_id: "C-1111", label: "A. Nair, captain" },
  { user: "c1157.reddy", role: "crew", crew_id: "C-1157", label: "D. Reddy, captain" },
  { user: "c1167.iyer", role: "crew", crew_id: "C-1167", label: "R. Iyer, captain" },
  { user: "c1191.nair", role: "crew", crew_id: "C-1191", label: "S. Nair, first officer" },
  { user: "c1460.krishnan", role: "crew", crew_id: "C-1460", label: "S. Krishnan, cabin crew" },
  { user: "c1123.verma", role: "crew", crew_id: "C-1123", label: "A. Verma, captain" },
  { user: "c1177.rao", role: "crew", crew_id: "C-1177", label: "S. Rao, captain" },
  { user: "c1211.bose", role: "crew", crew_id: "C-1211", label: "L. Bose, first officer" },
  { user: "c1242.iyer", role: "crew", crew_id: "C-1242", label: "D. Iyer, first officer" },
  { user: "c1184.chandra", role: "crew", crew_id: "C-1184", label: "K. Chandra, first officer" },
  { user: "c1247.reddy", role: "crew", crew_id: "C-1247", label: "I. Reddy, first officer" },
  { user: "c1341.chandra", role: "crew", crew_id: "C-1341", label: "H. Chandra, purser" },
  { user: "c1357.gupta", role: "crew", crew_id: "C-1357", label: "A. Gupta, purser" },
  { user: "c1530.malhotra", role: "crew", crew_id: "C-1530", label: "K. Malhotra, cabin crew" },
  { user: "c1542.krishnan", role: "crew", crew_id: "C-1542", label: "A. Krishnan, cabin crew" },
];

let cached: Promise<DemoAccount[]> | null = null;

export function demoAccounts(): Promise<DemoAccount[]> {
  cached ??= api<{ accounts: DemoAccount[] }>("/api/accounts/demo", { auth: false })
    .then((r) => (r.accounts?.length ? r.accounts : FALLBACK))
    .catch(() => FALLBACK);
  return cached;
}
