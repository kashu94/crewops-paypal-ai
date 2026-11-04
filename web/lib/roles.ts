export type Role =
  | "crew"
  | "reporter"
  | "controller"
  | "approver"
  | "auditor"
  | "admin"
  | "super_admin"
  | "speaker";

export const ROLES: Role[] = ["crew", "reporter", "controller", "approver", "auditor", "admin", "super_admin", "speaker"];

export type Me = {
  user: string;
  role: Role;
  crew_id: string | null;
  desk: { desk_id: string; name: string } | null;
  clock: string;
  tools: string[];
  name?: string;
  /** The first and last day of the demo roster. */
  days?: { first: string | null; last: string | null };
};

export const ROLE_LABEL: Record<Role, string> = {
  crew: "Crew",
  reporter: "Station reporter",
  controller: "Controller",
  approver: "Approver",
  auditor: "Auditor",
  admin: "Admin",
  super_admin: "Super admin",
  speaker: "Ops-room speaker",
};

export const HOME: Record<Role, string> = {
  crew: "/crew/",
  reporter: "/operations/",
  controller: "/desk/",
  approver: "/approvals/",
  auditor: "/disruptions/",
  admin: "/admin/",
  super_admin: "/admin/",
  speaker: "/speaker/",
};

export const homeFor = (role: Role | string | undefined) => HOME[(role as Role) ?? "controller"] ?? "/desk/";

export type NavItem = {
  href: string;
  label: string;
  hint: string;
  icon: string;
  count?: "approvals" | "queue";
  external?: boolean;
};
export type NavGroup = { group: string; items: NavItem[] };

const I = {
  desk: { href: "/desk/", label: "Desk", hint: "Problems, callouts and your desk's disruptions", icon: "desk" },
  disruptions: { href: "/disruptions/", label: "Disruptions", hint: "Every report, by desk and state", icon: "disruptions" },
  approvals: { href: "/approvals/", label: "Approvals", hint: "Plans and resolutions waiting for a second person", icon: "approvals", count: "approvals" },
  cover: { href: "/cover/", label: "Find cover", hint: "Every legal way to fill a seat", icon: "cover" },
  report: { href: "/report/", label: "Report a disruption", hint: "Work out what it breaks and who to tell", icon: "report" },
  leave: { href: "/report/", label: "Book planned leave", hint: "Leave, training and medical days ahead", icon: "report" },
  operations: { href: "/operations/", label: "Operations", hint: "A week of flights, who flies them and who is out", icon: "operations" },
  myReports: { href: "/disruptions/", label: "My reports", hint: "Reports you filed and where they stand", icon: "disruptions" },
  gates: { href: "/gates/", label: "Gates", hint: "Who boards where, and what a closure moves", icon: "gates" },
  assistant: { href: "/assistant/", label: "Assistant", hint: "Ask by text or voice; every figure checked", icon: "assistant" },
  activity: { href: "/activity/", label: "Activity", hint: "Who changed what, and refused questions", icon: "activity" },
  limits: { href: "/limits/", label: "Duty limits", hint: "The limits every seat is judged against", icon: "limits" },
  crew: { href: "/crew/", label: "My roster", hint: "Your calendar, flights, limits and offers", icon: "crew" },
  messages: { href: "/messages/", label: "Messages", hint: "Offers and roster changes sent to you", icon: "messages" },
  admin: { href: "/admin/", label: "Admin", hint: "Accounts, alerts, roster upload and limits", icon: "admin" },
  board: { href: "/board/", label: "TV board", hint: "The ops-room wall", icon: "board", external: true },
  speaker: { href: "/speaker/", label: "Speaker", hint: "The ops-room speaker", icon: "speaker", external: true },
} satisfies Record<string, NavItem>;

const OPS_ROOM: NavGroup = { group: "Ops room", items: [I.board, I.speaker] };

export function navFor(role: Role): NavGroup[] {
  switch (role) {
    case "crew":
      return [{ group: "Me", items: [I.crew, I.messages, I.assistant] }];
    case "reporter":
      return [{ group: "Station", items: [I.operations, I.report, I.myReports, I.assistant] }, OPS_ROOM];
    case "controller":
      return [{ group: "Crew control", items: [I.desk, I.disruptions, I.assistant] }, OPS_ROOM];
    case "approver":
      return [{ group: "Second pair of eyes", items: [I.approvals, I.disruptions, I.assistant] }, OPS_ROOM];
    case "auditor":
      return [
        { group: "Read only", items: [I.disruptions, I.approvals, I.assistant] },
        { group: "Oversight", items: [I.activity, I.limits] },
        OPS_ROOM,
      ];
    case "admin":
    case "super_admin":
      return [
        { group: "Administration", items: [I.admin, I.disruptions, I.leave, I.limits] },
        { group: "Oversight", items: [I.activity, I.assistant] },
        OPS_ROOM,
      ];
    default:
      return [OPS_ROOM];
  }
}

export const DESK_ROLES: Role[] = ["controller", "approver", "auditor", "admin", "super_admin"];
export const isDeskRole = (r: Role) => DESK_ROLES.includes(r);
