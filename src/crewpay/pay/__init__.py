"""Crew pay: an approver confirms each approval by paying for the cover with PayPal Checkout;
then the crew member who covers the seat is paid through PayPal Payouts."""

from crewpay.pay.funding import Funding, FundingError, FundingStatus
from crewpay.pay.paypal import PayPalClient, PayPalError, PayPalUnavailable
from crewpay.pay.payroll import CREW_PAY, PayConfig, PayError, Payroll, PayStatus

__all__ = [
    "CREW_PAY",
    "Funding",
    "FundingError",
    "FundingStatus",
    "PayConfig",
    "PayError",
    "PayPalClient",
    "PayPalError",
    "PayPalUnavailable",
    "PayStatus",
    "Payroll",
]
