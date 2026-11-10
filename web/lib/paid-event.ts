/** Said on the page the moment PayPal confirms an approval, so the payout conduit can play at
 * once instead of waiting for the next refresh. */
export const APPROVAL_PAID = "crewpay:approval-paid";

export type ApprovalPaid = { planId: string };

export function announcePaid(planId: string) {
  window.dispatchEvent(new CustomEvent<ApprovalPaid>(APPROVAL_PAID, { detail: { planId } }));
}
