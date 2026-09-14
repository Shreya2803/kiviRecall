# The World — Kavya Iyer, 12 Weeks

Written before the corpus. Everything in `generate_corpus.py` is drawn from this
document; nothing in the generator invents a fact that isn't decided here first.

## Who

**Kavya Iyer** — Senior Product Manager at **Finloop Technologies**, a ~80-person
Series B fintech in Bengaluru building checkout and payments infrastructure for D2C
merchants. She dictates constantly and everywhere — Slack, email, her notes app, voice
memos to herself between meetings. This corpus is twelve weeks of that: 2026-01-05
through 2026-03-27.

**Colleagues**
- **Rohit Sharma** — Engineering Lead, Project Tara.
- **Arun Kumar** — Backend engineer, Project Tara. Rotates to the platform team in
  week 5; hands his work to Meera.
- **Meera Pillai** — Backend engineer. Joins Tara in week 5 as Arun ramps her up;
  sole owner of Tara's backend by week 7.
- **Priya Nair** — Design Lead. Owns the design system throughout; picked up the
  brand refresh in week 8 on top of it.
- **Sanjay Reddy** — Engineering Manager. Takes ownership of the Kavach rollout from
  Kavya in week 8.
- **Divya Krishnan** — Customer Success Lead. Negotiates the Veriscan renewal down;
  picks up the Merchant Onboarding Revamp after Arun's rotation.
- **Vikram Nath** — VP Product, Kavya's manager.
- **Ramesh** — Account manager at CloudEdge (external).

**Vendors**
- **Veriscan** — KYC/AML verification vendor. Contract renewal is a running thread.
- **CloudEdge** — cloud infra/hosting vendor. API rate limits become a blocker.
- **TestForge** — QA/testing vendor, used for Tara's test cycles. Minor presence.

## Projects

- **Project Tara** — the merchant-facing payment gateway migration (Finloop's
  internal name for retiring the legacy gateway, "PayBridge," in favour of a unified
  PSP abstraction). The central thread of the whole twelve weeks.
- **Project Kavach** — a fraud/risk screening feature. Kavya scopes it in week 1,
  hands it to Sanjay in week 8 when Tara eats her bandwidth.
- **Merchant Onboarding Revamp** — a smaller, lower-priority self-serve onboarding
  flow. Owned by Arun until his rotation, then picked up by Divya as a side
  initiative — nobody's full-time focus, which is exactly why its ownership drifts.

## Timeline

- **Week 1 (Jan 5–11)** — Q1 planning. Tara migration plan finalised, target launch
  **Feb 20**. Kavach scoping begins; Kavya is driving it. Arun owns Onboarding Revamp.
- **Week 2 (Jan 12–18)** — Veriscan sends the renewal notice: **12% cost increase**.
- **Week 3 (Jan 19–25)** — Design system ownership confirmed: **Priya owns the
  design system.**
- **Week 4 (Jan 26–Feb 1)** — Tara load testing starts hitting **CloudEdge API rate
  limits** — a real blocker, no fix yet.
- **Week 5 (Feb 2–8)** — Arun mentions he's rotating to the platform team. Meera is
  introduced on Tara backend, starts shadowing Arun's tickets.
- **Week 6 (Feb 9–15)** — Handover in progress: Arun documents his open items, Meera
  starts picking them up directly. Divya opens negotiation with Veriscan, targeting
  below 12%. CloudEdge rate limit issue escalated to Ramesh.
- **Week 7 (Feb 16–22)** — A critical bug surfaces in Tara QA. Launch **slips from
  Feb 20 to March 10**. Handover completes: Meera is now sole owner of Tara's
  backend. Arun's rotation means Onboarding Revamp needs a new owner — Divya starts
  picking up loose ends.
- **Week 8 (Feb 23–Mar 1)** — Kavya hands Kavach's rollout to Sanjay as her time
  goes entirely to the Tara relaunch — no date attached yet, just the ownership
  change. Priya starts leading the brand refresh in addition to the design system
  (mentioned once in Devanagari-script Hindi-English: "प्रिया अब brand refresh भी
  lead कर रही हैं").
- **Week 9 (Mar 2–8)** — CloudEdge agrees a new rate limit (100 → 500 req/s),
  effective immediately. Veriscan's new terms are formally signed at **8%**, not 12%.
- **Week 10 (Mar 9–15)** — A partner-integration delay pushes Tara **again, from
  March 10 to March 24**. Sanjay sets the Kavach rollout date: **March 15**.
- **Week 11 (Mar 16–22)** — Tara QA sign-off from Meera. Kavach launches on schedule,
  March 15.
- **Week 12 (Mar 23–27)** — Tara finally launches, **March 24**. Retro scheduled.
  Q2 planning mentioned in passing (noise, not yet a decision).

## Planted structures

**Distributed facts** (no single dictation contains the full answer — recovered only
by joining on the resolved entity):

1. **Kavach: who owns it, and when does it launch?** Week 1 establishes Kavya as
   driving it (no date). Week 8 gives the ownership change to Sanjay (still no date).
   Week 10 gives the date, March 15, attached to "the rollout" without restating who
   owns it. Three dictations, three different weeks, no overlap.
2. **Veriscan's renewal cost.** Week 2: the 12% increase notice. Week 6: Divya is
   negotiating, target below 12%, no final number. Week 9: signed at 8%. (This one
   doubles as a contradiction — realistic; most distributed facts in real dictation
   are exactly the ones that also change.)
3. **CloudEdge's rate limit.** Week 4: hitting the limit, blocking load tests, no
   contact named. Week 6: escalated to Ramesh, no resolution yet. Week 9: new limit
   agreed, 500 req/s, effective immediately, named only as "the new CloudEdge limit."
4. **Priya's expanding scope** (crosses a language boundary). Week 3, English: "Priya
   owns the design system." Week 8, Devanagari-script Hindi-English: "प्रिया अब brand
   refresh भी lead कर रही हैं" (Priya is now leading the brand refresh too) — same
   person, alias in a second script. Week 8/9, English or Hinglish: scope has grown
   enough that she's asked for a second designer. Entity resolution must fuse
   "Priya"/"Priya Nair" and "प्रिया" into one entity before this fact is joinable at
   all.

**Contradictions that resolve over time:**

1. **Tara's launch date** — Feb 20 → March 10 (week 7, critical QA bug) → March 24
   (week 10, partner integration delay). A two-hop supersession chain.
2. **Merchant Onboarding Revamp's owner** — Arun (week 1) → Divya (week 7, after
   Arun's rotation leaves it unowned for a beat).
3. **Veriscan's renewal cost** — 12% (week 2) → 8% (week 9, after Divya's
   negotiation in week 6).

**The handover:** Arun Kumar → Meera Pillai on Project Tara's backend, weeks 5–7.
Mentioned across several dictations from both the outgoing and incoming side, and
from Rohit as the eng lead observing the transition.

**Excluded-category content (~15 health/emotional/personal, ~5 colleague-opinion or
financial):** Kavya, like anyone dictating to a device all day, narrates things that
aren't for the record — exhaustion, a doctor's appointment, a fight with her partner,
her son's parent-teacher meeting, and occasional unfiltered opinions about colleagues
("Rohit never listens in meetings") that must never be stored even though the same
colleague's factual role elsewhere absolutely should be.

**Ambiguous referents (~10–12):** pronouns and vague references with two or more
plausible antecedents in context — "tell him," "she said it's fine," "that vendor
thing" — where the correct behaviour is to drop the claim, not guess which colleague
or vendor it means.

## Composition target (of ~500 records)

| Bucket | Share | Approx. count |
|---|---|---|
| Durable work content (standalone) | 30% | 150 |
| Transient noise | 50% | 250 |
| Distributed facts | 8% | 40 |
| Contradictions | 5% | 25 |
| Excluded category | 4% | 20 |
| Ambiguous referents | 3% | 15 |

## Language mix (of ~500 records)

| Style | Share | Approx. count |
|---|---|---|
| English | 55% | 275 |
| Hindi-English code-mixed (Romanised) | 30% | 150 |
| Tamil or Kannada mixed with English (Romanised) | 10% | 50 |
| Devanagari script mixed with English | 5% | 25 |
