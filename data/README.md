# Data

Two inputs, both CSV, both one row per event or per person. The example files
are committed and let the script run out of the box; the real exports are
gitignored.

| File | Committed | Source |
| --- | --- | --- |
| `orders_example.csv` | yes | Handwritten, `example.com` addresses |
| `shopify_orders_example.csv` | yes | Handwritten in the layout of a Shopify orders export, `example.com` addresses |
| `consent_example.csv` | yes | Handwritten, `example.com` addresses |
| `orders.csv` | **no** | Store order export |
| `consent.csv` | **no** | ESP / signup form export |

Every CSV here except the `*_example.csv` files is gitignored, and so is every
list the scripts write into `reports/`, because they contain personal data. Committing a
subscriber list to a public repository is a personal data breach, not an
oversight — there is no anonymisation step that makes it acceptable, so the
files simply never enter git.

## `orders.csv`

| Column | Type | Notes |
| --- | --- | --- |
| `email` | string | Lowercased and trimmed on read |
| `order_date` | `YYYY-MM-DD` | One row per order, not per customer |
| `order_value_eur` | float | Optional; missing is read as 0 |

Extra columns are ignored, so an export can be passed through unmodified as
long as these three exist.

### Or the Shopify export as it is

`Orders > Export`, CSV. No editing needed: the scripts recognise the file by
its columns (`Name`, `Email`, `Created at`, `Total`) and then

- fold the line items of each order into one order (the export has one row per
  line item, with the order total only on the first one), so a five-item order
  is not counted as five orders;
- skip orders that were cancelled (`Cancelled at` filled in), refunded or
  voided (`Financial Status`), because they are not purchases;
- take the order date from `Created at` and the value from `Total`.

A file saved again from Excel starts with an invisible byte-order mark; that is
handled too.

## `consent.csv`

| Column | Type | Notes |
| --- | --- | --- |
| `email` | string | Lowercased and trimmed on read |
| `consent_status` | string | Only exactly `opted_in` passes the gate |
| `consent_source` | string | Required for `opted_in`; written on the profile |
| `consent_timestamp` | ISO 8601 UTC | Required for `opted_in`, on or before `--as-of`; written on the profile |

An email present in `orders.csv` but absent from `consent.csv` is suppressed as
`unknown`. That is deliberate: absence of a record is not permission. An
`opted_in` row with no source, or no valid date, is suppressed as
`opted_in_without_proof`, because a consent the shop cannot show does not
count. See
[`../docs/consent.md`](../docs/consent.md).

## Reproducing the example run

```bash
python src/sync_klaviyo.py --as-of 2026-06-01
```

`--as-of` pins the reference date so recency, and therefore every lifecycle
stage, is reproducible. Without it the script uses today and the example
output drifts — every customer eventually ages into `Churned`.

Expected: 9 customers in, 6 eligible across all five stages, 3 suppressed (one
`opted_out`, one with no consent record, one `opted_in` with no source or date).
The report counts the eligible ones by sign-up source: five confirmed through
the double opt-in form, one through Shopify's checkout checkbox, which skips
the confirmation.
