# Lifecycle Automation Lab

Three email flows built in Klaviyo on a Shopify development store: welcome with
double opt-in, abandoned cart and win-back. I set up the accounts,
authenticated the sending domain and sent the flows for real, to see what
actually happens between "the flow is drawn" and "the email arrives".

## What is real and what is not

| Real | Not real |
| --- | --- |
| A Klaviyo account with three live flows and seven emails | The store is a Shopify development store: password-protected, with no real customers |
| A sending domain I authenticated myself (DKIM and DMARC), checked against the public DNS records | The orders in it were seeded by me |
| A double opt-in list, and a rule that no profile is touched without consent | One subscriber, three delivered emails, no clicks and no sales |

So this repository shows that the mechanism works end to end. It does not show
performance: there is no open rate, click rate or revenue worth reporting, and
[the results](reports/results.md) report counts, never rates.

## What I found by running it

These only showed up because the flows were actually sent, not just drawn.

1. **Smart Sending skipped the first abandoned-cart email.** A welcome email
   had gone out five hours earlier, so Klaviyo held back the cart email. Nothing
   in the reports said so: no send, no error. One screen in Klaviyo names the
   reason. The next email in the same flow went out a day later, once the window
   had passed.
2. **Double opt-in on a list does not protect the list.** A sign-up through
   Shopify's own newsletter field arrived in Klaviyo as subscribed straight
   away, with no confirmation email. The setting only protects sign-ups that
   come through the Klaviyo form.
3. **A green "Verified" was not my configuration.** The first domain I tried
   was parked on a resale marketplace that answered every DNS check. Klaviyo
   showed the domain as verified although I had configured nothing. Since then
   I check the DNS records myself instead of trusting the status column.

One thing is still unproven: I have not yet seen the abandoned-cart flow stop
when the customer completes the order, because Smart Sending got in the way of
the one test I ran. The results describe the test that would settle it.

Full write-up, including what else broke: [`reports/results.md`](reports/results.md).

## Checking an email before it goes live

[`src/pre_send_check.py`](src/pre_send_check.py) checks the seven emails before
their HTML is pasted into Klaviyo: every personalisation tag has a fallback,
every link is a real https address, any hand-typed UTM follows the
[taxonomy](docs/flows.md#measurement), the unsubscribe link is there, the
subject fits on a phone (50 characters) and the preview text adds something to
it. CI runs it on every change.

Its first run flagged all seven emails that were live. Six greeted the reader
with `Ciao {{ first_name|default:'' }},`, which reads "Ciao , grazie..." for
anyone who signed up without a first name, and the three cart emails had no
fallback if Shopify's checkout link is missing. Both are fixed in this
repository; the emails in Klaviyo change when the new HTML is pasted in.

## The flows

| # | Flow | Trigger | Steps |
| --- | --- | --- | --- |
| 1 | Welcome | Added to the double opt-in list, so only after confirmation | Day 0 → wait 3 days → Day 3 |
| 2 | Abandoned cart | `Checkout Started`, with the filter *Placed Order zero times since starting this flow* re-checked before every send | 4h → email → 20h → email → 2 days → email |
| 3 | Win-back | Entry into the `At risk` segment, which reads a profile property written by [`src/sync_klaviyo.py`](src/sync_klaviyo.py) | Day 0 → wait 7 days → Day 7 |

Specification and the reason for each delay: [`docs/flows.md`](docs/flows.md).
Consent design: [`docs/consent.md`](docs/consent.md).
Domain authentication: [`docs/deliverability.md`](docs/deliverability.md).

![Abandoned cart flow canvas](assets/flow-abandoned-cart.png)

![Abandoned-cart trigger with the Placed Order exit filter](assets/flow-exit-condition.png)

![Win-back flow canvas](assets/flow-winback.png)

More screenshots (welcome flow, segment, double opt-in setting, DNS answers,
Shopify admin) are in [`assets/`](assets).

## How a customer enters the win-back

Each customer gets a lifecycle stage based on the date of their last order.
The stage is saved on the Klaviyo profile, a Klaviyo segment collects everyone
who is "At risk", and entering that segment starts the win-back flow.

```
order history + consent  ->  lifecycle stage on the profile  ->  "At risk" segment  ->  win-back flow
```

| Stage | Rule |
| --- | --- |
| New | last order ≤ 90 days ago, 1 order |
| Repeat | last order ≤ 90 days ago, ≥ 2 orders |
| At risk | last order 91-180 days ago |
| Dormant | last order 181-365 days ago |
| Churned | last order > 365 days ago |

The thresholds are the same as in my
[CRM retention analysis](https://github.com/errer441122/digital-campaign-performance-dashboard).

Consent comes first: only profiles that have already opted in are updated, and
only if the consent record says where and when the consent was given, because
under GDPR the shop must be able to show it. The source and date are written on
the profile next to the lifecycle stage. Everyone held back is listed in a
report, which also counts subscribers by sign-up source: that is where the
Shopify checkbox subscribers from finding 2 show up. Nothing in this project
can subscribe a person who did not ask for it.

## Real-World Business Application (Next Steps)

What I would do on a store with real customers.

1. **Turn Smart Sending off on high-intent flows.** Smart Sending stops a
   person from receiving two emails within a set window. That is right for
   newsletters, but an abandoned cart is the moment the customer is closest
   to buying. In this lab I left it on to document the behaviour, and it cost
   the first cart email. On a real store I would switch it off for abandoned
   cart and order emails, and keep it on for campaigns.
2. **Use the segments to decide who gets a discount.** A discount sent to
   everyone is paid mostly by people who would have bought anyway. The
   win-back offer here goes only to customers whose last order was 91 to 180
   days ago, and only once. Recent and loyal customers get content, not money
   off.
3. **Treat consent as a rule of the whole system, not one setting.** Double
   opt-in on the list was not enough: the Shopify newsletter checkbox went
   around it. On a real store I would send every sign-up source through the
   same confirmed opt-in, keep the date and source of each consent, and check
   the list for unconfirmed profiles regularly. Under GDPR the shop has to be
   able to show how each person consented.
4. **Measure with real traffic.** With one subscriber there is nothing to
   measure. The next step is a store with real visitors, where each flow can be
   judged on revenue per recipient and unsubscribe rate.

## Run the example

The lifecycle rules can be tried on the example data without any account:

```bash
python -m unittest discover -s tests
python src/pre_send_check.py
python src/sync_klaviyo.py --as-of 2026-06-01
```

The last one is a dry run: it prints who would be updated and who is held back
for missing or unprovable consent, and sends nothing. Writing to a real Klaviyo account needs an
API key and the `--live` flag.

## Boundaries

- No purchased, scraped or imported lists. The only recipient confirmed through
  double opt-in.
- The example CSVs use `example.com` addresses, which cannot receive mail.
- The numbers are a snapshot from 31 August 2026.

## How I built this

I am a Digital Marketing student, not a developer. The Python scripts were
written mostly by AI coding assistants. My part was setting up and running the
Klaviyo and Shopify accounts and the DNS, defining the rules (consent, triggers,
timing), checking what was actually sent and deciding what to report, including
what did not work.

## Licence

MIT. See [LICENSE](LICENSE).
