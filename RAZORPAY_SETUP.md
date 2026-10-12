# Razorpay setup for EventAI

Razorpay Standard Checkout is integrated into this project. The server creates INR orders, verifies checkout signatures, fetches payment status directly from Razorpay, and accepts signed webhooks. Only captured, unrefunded payments are eligible for **the owning organizer's ticket approval**. Administrators publish events and manage payment-account reconciliation; they cannot approve tickets.

This package contains no Razorpay credentials. Automated provider tests use mocked responses, so no real money was collected or refunded during development. Complete a Razorpay Test Mode checkout and webhook delivery test on your deployment before accepting real payments.

## 1. Configure Test Mode locally

Copy `.env.example` to `.env`, then set:

```dotenv
APP_ENV=development
DEMO_MODE=1
PAYMENT_PROVIDER=razorpay
RAZORPAY_KEY_ID=rzp_test_REPLACE_WITH_YOUR_KEY_ID
RAZORPAY_KEY_SECRET=REPLACE_WITH_YOUR_KEY_SECRET
RAZORPAY_WEBHOOK_SECRET=REPLACE_WITH_A_SEPARATE_RANDOM_SECRET
```

Generate API keys in your Razorpay dashboard's Test Mode. Use a separate random webhook secret of at least 16 characters. Keep `.env` on your computer/server; never commit or share it. Restart `python app.py` after changing settings. No new Python dependency is needed for this integration.

`PAYMENT_PROVIDER=demo` uses the earlier local simulator. `disabled` blocks paid checkout. With `razorpay`, simulated paid booking APIs cannot bypass Razorpay verification. Free events still submit organizer-reviewed requests.

## 2. Configure capture and webhooks

Configure automatic payment capture in Razorpay. An authorized payment alone does not qualify for a ticket.

Create a webhook pointing to:

```text
https://YOUR-DOMAIN/api/razorpay/webhook
```

Set its secret to the exact `RAZORPAY_WEBHOOK_SECRET` on your server. Subscribe to:

- `payment.captured`
- `payment.authorized`
- `payment.failed`
- `order.paid`
- `refund.processed`

Your webhook URL must be publicly reachable over HTTPS. Localhost is not publicly reachable; use a deployment or a development tunnel for end-to-end webhook testing. The browser verification flow and the Refresh Razorpay status button provide recovery when a callback is lost. Webhook retries are deduplicated, and duplicate browser callbacks do not issue duplicate tickets.

## 3. Test the full flow

1. Sign in as an attendee, open an upcoming paid event, and choose tickets.
2. Continue to Razorpay. When the order is created, seats are held for 15 minutes and disappear from available inventory immediately.
3. Complete a Razorpay Test Mode payment. The server verifies the signature, order ID, exact amount, currency and capture status. A captured payment extends the hold until the event starts while awaiting organizer approval.
4. Sign in as the event's organizer. Open Studio → Ticket approvals. Approve the verified request to issue a QR ticket, or reject it to release seats and flag the payment for refund.
5. Check the attendee's Tickets and Updates pages. Verify an unrelated organizer and the administrator cannot approve the request.
6. Test failed/cancelled checkouts, an expired hold, repeated callbacks, webhook delivery with the browser closed, and a refund. A late successful payment without available seats is flagged for refund and never oversells the event.

Demo and free requests hold seats until their 24-hour approval deadline or event start, whichever is earlier. Unpaid Razorpay holds expire after 15 minutes. Closing checkout does not prove payment failure, so the hold is retained until expiry or a verified failure. Availability excludes expired holds immediately, even if no worker is running.

## 4. Refunds and unresolved orders

**Refunds are not sent automatically.** Rejected captured payments are marked `refund_required`. The payment account administrator must issue the refund in the Razorpay dashboard, then click Refresh refund status or wait for the signed webhook. A verified full refund disables any issued QR ticket and adjusts the reward ledger once. A partial refund disables the ticket and is flagged for manual reconciliation; this version does not split tickets or support partial group admission. Reward reversal can make a balance negative if the earned points were already spent; later earnings offset that balance.

If order creation times out, the application does not blindly create another provider order. It retains an unresolved record with a unique receipt. In Admin → Payment account reconciliation, find that receipt in Razorpay Orders and use Recover order with its `order_…` ID. The server checks receipt, amount and currency before linking it. Do not retry a debit because the browser did not show success.

Keep the original Razorpay key available for unresolved transactions when rotating keys; the app blocks reconciliation with a different key. The application supports one active Razorpay account/key pair at a time.

## 5. Enable live payments

Complete your Razorpay account activation and Test Mode checks. Configure `.env.production.example` with a clean production database, HTTPS, trusted hostnames, SMTP and a random application secret. Then set `PAYMENT_PROVIDER=razorpay`, `DEMO_MODE=0`, `APP_ENV=production`, and the live API keys and live webhook secret. Live keys are blocked in development/demo mode.

Run `python release_check.py`, start `python run_production.py` behind an HTTPS reverse proxy, and verify webhook delivery. Browser/mobile checkout, genuine provider transactions, refunds, operational monitoring and a security review still need verification on your server. Passing mocked tests is not a production certification.

## Official references

- [Razorpay Standard Checkout integration](https://razorpay.com/docs/payments/payment-gateway/web-integration/standard/integration-steps/)
- [Webhook signature validation](https://razorpay.com/docs/webhooks/validate-test/)
- [Razorpay Python signature verification implementation](https://github.com/razorpay/razorpay-python/blob/master/razorpay/utility/utility.py)

The implementation uses the server-stored order ID when checking the checkout signature; it never trusts the browser's amount or a client-supplied success flag.
