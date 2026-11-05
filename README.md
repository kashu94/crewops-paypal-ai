# CrewPay

Crew control for regional airlines that **pays the crew it calls in**: an AI agent on Amazon
Bedrock finds legal cover for a sick pilot in seconds, a second person approves it, and the crew
member who steps in is paid through **PayPal Payouts** the moment the plan is approved. When
they must fly in first, their seat is bought with **PayPal Checkout**.

At 04:10 a first officer reports sick. Their trip leaves at 06:30. Today a controller digs
through the roster, duty-hour sheets and licence records to find someone rested, rated and
close enough to the airport, then a separate payroll team works out, days or weeks later, what
that person is owed for being called in: callout allowance, service allowance, travel. Crew
chase the money; finance chases the paperwork.

CrewPay does both in one flow, and shows its working:

1. **Report** a disruption (by text, voice or a photo of the sick note).
2. **Find cover**: the rules engine checks every candidate against nine duty-time rules and
   ranks the legal ones by what they cost.
3. **Approve**: a controller submits a plan; a different person approves it. The rules are
   checked again at approval.
4. **Pay**: the payroll turns the engine's cost lines into a PayPal payout to that crew member,
   tracks it to settled through PayPal webhooks and status checks, and tells them they were paid.
   If cover is based at another airport, approving the plan also creates a PayPal order for their
   seat on the positioning flight, which an approver pays with the PayPal button.

> The model reads, plans and explains. It never does the arithmetic, and it never decides to
> move money on its own.

Every crew id, duty hour, verdict, rupee and dollar comes from a deterministic engine and the
rate card. A verifier checks each figure in an answer against the tools' results before anyone
sees it.

## Who it is for

- **Crew controllers** at regional airlines, who fill seats at short notice against strict
  duty-time rules.
- **Approvers / duty managers**, who must sign off every roster change and every large payment.
- **Pilots and cabin crew**, who cover at a few hours' notice and today wait for the money.
- **Finance and auditors**, who need every payment tied to an approved decision, a rate and a
  PayPal transaction id.

## How PayPal is used

| What | PayPal capability | Where |
|---|---|---|
| Pay the crew member who covers a seat, the moment the plan is approved | **Payouts API** (`POST /v1/payments/payouts`), one batch per plan | [`src/crewpay/pay/payroll.py`](src/crewpay/pay/payroll.py) |
| Never pay twice, even on a retry or a timeout | `sender_batch_id` and `PayPal-Request-Id` set to the payment id | [`src/crewpay/pay/paypal.py`](src/crewpay/pay/paypal.py) |
| Follow each payout to paid, unclaimed, failed or returned | **Webhooks** (`PAYMENT.PAYOUTS-ITEM.*`), each one verified with `verify-webhook-signature`; plus a status check every minute (`GET /v1/payments/payouts/{id}`) | [`src/crewpay/api/app.py`](src/crewpay/api/app.py), [`src/crewpay/api/ticker.py`](src/crewpay/api/ticker.py) |
| Take back a payment nobody claimed | **Cancel unclaimed payout item** | `cancel_payment` tool |
| Buy the seat on the flight that positions cover from another base | **Orders v2** (`POST /v2/checkout/orders`, then `/capture`), created by the server from the rate card; the payer approves with the **PayPal JavaScript SDK** button or the order's approve link | [`src/crewpay/pay/seats.py`](src/crewpay/pay/seats.py), [`web/components/paypal-button.tsx`](web/components/paypal-button.tsx) |
| Never buy a seat twice, and finish a payment if the browser closes | `PayPal-Request-Id` on create and capture; **webhooks** `CHECKOUT.ORDER.APPROVED` and `PAYMENT.CAPTURE.*`, and the status check, capture an approved order | [`src/crewpay/pay/seats.py`](src/crewpay/pay/seats.py) |
| Ask about pay in plain words ("has D. Reddy been paid for T-1018?", "pay S. Rao's positioning seat") | The agent's `crew_payments`, `payment_status` and `positioning_seats` tools read the ledger and PayPal; `pay_seat` takes the payment once the payer has approved it in PayPal, and hands over the approve link if not | [`src/crewpay/agent/pay_tools.py`](src/crewpay/agent/pay_tools.py) |

Controls around the money:

- **Four eyes on every payment**: no payout without an approved plan, and the approver is never
  the controller who proposed it.
- **A cap** (`CREWPAY_PAY_CAP`, default $500): a larger payment is held until a *second*
  approver, not the one who approved the plan, releases it.
- **Only engine-costed lines are paid**: callout, service allowance and travel allowance.
  Airline costs (a positioning seat, delay costs) are listed on the payment, not paid to crew.
- **A person approves every seat purchase in PayPal**: the server sets the amount and payee and
  captures only an order PayPal says the payer approved; the agent can never approve one.
- **Sandbox only**: the settings refuse any PayPal address other than the sandbox.
- **Rupees are the cost of record**; payouts are made in US dollars at a fixed demo rate
  (`CREWPAY_FX_INR_PER_USD`, default 84), and both are kept on every payment.

Without PayPal credentials the same steps run in a clearly marked **simulated** mode, so the
project works on any laptop.

## How AI is used

| Job | Model (Amazon Bedrock, Converse API) | How |
|---|---|---|
| Planning each answer: which engine tools to call, then the reply | **Amazon Nova 2 Lite** | Tool calling over 59 typed tools, each with role checks in code |
| Sorting each question (absence, delay, cover, legality, change, pay, lookup) and flagging attacks on the assistant | **Amazon Nova Micro** | Structured output (a forced tool with a strict JSON schema); keyword rules take over if the model is unavailable |
| Reading photos: sick notes, roster swaps, certificates, departures boards | **Nova 2 Lite** (vision) | Each photo fills a fixed form; code decides what happens, and a person confirms |
| The shift brief, the message to passengers on a delayed flight, the shift handover | **Nova 2 Lite** | Structured output, then the same evidence check as every answer; a text that fails it is replaced by a template built from the same facts |
| Spoken questions | The browser's speech recognition | Only the words heard are sent, like a typed question |

The agent can **act**, not only advise: it can report an absence, start a callout, submit a plan
and, for approvers, release a held payment or take the payment for a seat once it is approved
in PayPal. Every action goes through the same tools, role
checks and four-eyes rules as the buttons in the console.

## Architecture

```
  Web console (Next.js, static)        MCP clients          PayPal webhooks
            │                               │                      │
            ▼                               ▼                      ▼
  ┌──────────────────────────────────────────────────────────────────────────┐
  │ FastAPI server                                                           │
  │  agent: router (Nova Micro) → advisor (Nova 2 Lite, tools) → verifier     │
  │  tools: typed, role-checked; the same ones the buttons call              │
  │  rules engine: duty-time legality, cover ranking, costs (pure Python)     │
  │  plans: submit → approve (four eyes) ──► payroll ──► PayPal Payouts       │
  │                                  └──► seat order ──► PayPal Checkout      │
  │  background loop: callouts, escalation, brief, payout status              │
  └──────────────────────────────────────────────────────────────────────────┘
                     │                                   │
                     ▼                                   ▼
             Amazon Bedrock (Nova)              PayPal sandbox REST API
```

## Try it

**Hosted demo:** _link added after deploy_. Pick any identity on the sign-in page: no
passwords. A quick path through it:

1. Sign in as **Ananya Iyer (Controller)**. On **Desk**, the watch has already found that
   Captain A. Nair's medical lapses before `T-1018`. Or ask the **Assistant**: "Captain
   C-1111 is sick for T-1018, who do I use?"
2. Choose cover in **Find cover** and submit the plan.
3. Sign out, sign in as **Meera Bhatt (Approver)**, open **Approvals** and approve the plan.
4. Open **Crew pay**: the payout is already with PayPal, with its rupee lines, dollar amount and
   PayPal batch id. Within a minute it shows **Paid**. A payment over the cap waits instead:
   sign in as a second approver (**Karan Mehta**) to release it.
   To see a seat bought, have the controller cover T-1015's captain (C-1140) with **S. Rao**,
   who flies in on KF502: after approval, **Crew pay** shows the seat with the PayPal button.
5. Sign in as the crew member (**D. Reddy, Crew**): **My pay** shows the payment and **Messages**
   has the "Paid" notice. Ask the assistant "why was I paid that much?"

## Run it locally

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/) and Node.js 20+.

```bash
git clone https://github.com/kashu94/crewops-paypal-ai.git
cd crewops-paypal-ai
cp .env.example .env            # optional: add Bedrock and PayPal sandbox keys
uv sync
cd web && npm ci && npm run build && cd ..
uv run python -m crewpay.api    # http://localhost:8000
```

With no keys at all it still runs: payouts are simulated and the assistant says the model is
unavailable, while every screen, rule and approval works.

### PayPal sandbox

1. At [developer.paypal.com](https://developer.paypal.com/) open **Apps & Credentials**, make
   sure **Sandbox** is selected, and create an app named **`paypal-crewpay-sandbox`**. Put its client id and secret in `.env` as
   `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET`. The app's default sandbox business account
   pays the crew.
2. Optional: under **Testing tools > Sandbox accounts**, copy a *personal* account's email into
   `PAYPAL_SANDBOX_RECEIVER`, so every payout lands there and shows as **Paid**. Without it,
   each crew member is paid at their own address and PayPal keeps the money as **Unclaimed**.
3. Optional: copy a sandbox *business* account's email into `PAYPAL_SEAT_SELLER`, the airline
   that sells positioning seats. To pay for a seat, log in to PayPal with a sandbox *personal*
   account when the PayPal button opens.
4. Optional, when hosted: add a webhook to the app for
   `https://<your host>/api/paypal/webhook` with the `Payment payouts-item`, `Checkout order
   approved` and `Payment capture` events, and put its id in `PAYPAL_WEBHOOK_ID`.

### Amazon Bedrock

Give CrewPay its own credentials; it never reads your default AWS profile, and it needs no IAM
role. Every AWS resource it uses starts with `paypal-`:

1. In IAM create a user **`paypal-crewpay-bedrock`** with no console access and no groups, and
   give it one inline policy, **`paypal-crewpay-invoke`**, from
   [`deploy/paypal-crewpay-bedrock-policy.json`](deploy/paypal-crewpay-bedrock-policy.json): it
   may only call the Nova models.
2. On that user's **Security credentials** tab, generate an **API key for Amazon Bedrock** and put
   it in `CREWPAY_BEDROCK_API_KEY`. (Or create an access key and set
   `CREWPAY_AWS_ACCESS_KEY_ID` / `CREWPAY_AWS_SECRET_ACCESS_KEY`, or a profile named
   `paypal-crewpay` in `CREWPAY_AWS_PROFILE`.)
3. Models default to `global.amazon.nova-2-lite-v1:0` and `amazon.nova-micro-v1:0` in
   `ap-southeast-2`; enable them under **Model access**. Check with:

```bash
uv run python -m crewpay.models.check
```

### Tests

```bash
uv run pytest
uv run ruff check .
```

### Deploy to Render

[`render.yaml`](render.yaml) describes one Docker web service, `paypal-crewpay-console`. In Render choose **New >
Blueprint**, point it at this repository, and fill in the secrets it asks for.

## The rules

Nine rules, modelled on Indian flight duty time limitations and simplified for a demo. Each
returns the numbers behind its verdict ("1h45m over the 12h limit for 4 sectors").

| Rule | Check |
|---|---|
| FDTL-01 | Flight duty period at most 13 h, 30 min less for each sector beyond the second |
| FDTL-02 | A duty touching 00:00-06:00 IST: at most 10 h and two night landings |
| FDTL-03 | At most 60 duty hours in any 7 calendar days |
| FDTL-04 | At most 100 block hours in any 28 calendar days |
| FDTL-05 | Rest of at least 12 h, and at least as long as the duty before it |
| FDTL-06 | Two clear days in a row in every 7 |
| FDTL-07 | Active, rated on the type, flying for their own airline |
| FDTL-08 | Every certificate valid on every date the duty touches |
| FDTL-09 | Called out from home base, or positioned there on a same-day flight with a free seat |

Limits live in [`data/seed/rules.json`](data/seed/rules.json). The demo operation is two weeks
of three fictional airlines based in Hyderabad (Sahyadri Air, Kaveri Flyers, Godavari Link);
every name in it is made up.

## Repository layout

```
src/crewpay/
  engine/      Rules engine: duty maths, legality, cover ranking, costs, delays, gates
  agent/       Router, advisor, verifier, guard, and the typed tools (pay_tools.py for pay)
  pay/         PayPal client (OAuth, Payouts, Orders, webhooks), the payroll, positioning seats
  ops/         Callouts, plans and approvals, reports, notices, escalation, brief
  models/      The Bedrock Converse client
  vision/      Photo intake
  api/         FastAPI server, background loop
  store/       The operation in memory
web/           The console (Next.js, exported as static files)
data/seed/     The demo operation
tests/         Unit and API tests
```

## License

[MIT](LICENSE)
