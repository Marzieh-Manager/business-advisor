<!--
  business_context/business.md
  ============================
  This file IS the product's moat. It is injected verbatim into the weekly
  advisor prompt, and it is the only reason the report can say something
  specific about THIS business instead of generic advice.

  SAMPLE FILE. Harbourline Coffee Co. is a fictional business, written so the
  showcase runs end to end without exposing a real client. To adapt the advisor
  to a new business, replace this file - nothing in the code needs to change.

  How to edit:
    - The headings are stable keys the prompt relies on - rename them only if
      you also update the prompt.
    - `[TODO: ...]` marks a real gap. Fill it or delete the line.
    - `(inferred)` marks something taken from source material rather than
      stated by the owner. Confirm it or remove it.
    - Prefer concrete, checkable detail over adjectives. "40 cafe accounts
      reordering weekly" is useful; "sales are good" is not.
-->

# Harbourline Coffee Co. — Business Context

## Business overview

- **Harbourline Coffee Co.**, a specialty coffee roaster in Sydney, trading since **2018**.
- Field: **wholesale coffee** to cafes and offices, plus a smaller **online retail** store.
- Scale: about **45 active wholesale accounts**, around **30 coffee lines** (blends, single
  origins, decaf) and a small range of equipment and accessories.
- Platform: **Shopify** for retail and wholesale ordering. Wholesale accounts can also order by
  phone or email; the team keys those in.
- Revenue split: roughly **70% wholesale / 30% retail** (owner's figure).
- Roasting: **four roast days a week** on one 15 kg roaster. Roast schedule is planned every
  Friday for the week ahead.

**Software ecosystem:** Shopify, Xero (accounting), a subscription app for retail customers,
and a shared spreadsheet for the roast schedule.

## Customer segments

- **Cafe accounts** — the core. Weekly standing orders, on **14-day statement terms**.
- **Office accounts** — smaller volume, monthly orders, often with a machine on loan.
- **Retail / subscription** — home customers on the website, about 300 active subscriptions.

## Revenue model & terms

- Wholesale is priced per kilo, with tiered pricing by weekly volume.
- New cafe accounts often get a **machine on loan** in exchange for a minimum weekly volume.
- Retail subscriptions ship every 2 or 4 weeks.

## Key people / roles

- **The owner** — runs sales, key accounts and green-bean buying. Still covers the roaster
  when the head roaster is off.
- **Priya** — head roaster, owns quality and the roast schedule.
- **Tom** — warehouse and dispatch, part-time.
- **Lena** — account manager for cafes, joined six months ago.

[TODO: who owns marketing? Currently the owner does it "when there is time".]

## Known recurring challenges

- **The owner is the backup for every role.** When anyone is away, the owner fills in, and
  sales calls stop that week.
- **Green-bean buying is done from memory.** There is no written reorder point; a popular
  single origin has run out twice this year. (inferred)
- **Late payers.** A few cafe accounts regularly pay past the 14-day terms.
- **Machine-on-loan accounts** that drop below their agreed minimum are not followed up.

## Current initiatives

- Testing an **office subscription** bundle (beans + machine service).
- Considering a **second roaster** to remove the four-day roasting limit.
- Planning to run **barista training sessions** for cafe staff as a loyalty tool.

## Goals

### What the owner has asked for (stated directly)

- An advisor that turns scattered thoughts from the week into **clear priorities**.
- To spend **less time in operations** and more time on new accounts.
- To be told plainly when an idea is **not worth doing**.

[TODO: no numeric goal yet - for example a target number of wholesale accounts or kilos per
week. Add one so prioritisation has a measurable anchor.]

## Advisor tone

**Direct** (default).

- Straight to the point, no throat-clearing.
- Every recommendation must attach to a specific fact about this business — never generic
  management advice.
- When something is not worth doing, **say no plainly**. The value of this report is in filtering.
- Sympathy is not needed; a system and a next step are.

## Topics the owner wants to be challenged on

- Buying a second roaster before the first one is fully used. (inferred)
- Covering every role personally instead of training a backup. (inferred)

## Operational vocabulary

- **Green beans** — unroasted coffee, bought from importers in 60 kg bags.
- **Roast schedule** — the weekly plan of what is roasted on which day.
- **Standing order** — a cafe's fixed weekly order.
- **Machine on loan** — an espresso machine placed in a cafe against a minimum weekly volume.
- **Cupping** — the tasting used to check roast quality.
