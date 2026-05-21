# Basic Capital FAQ — Plan Advisor & 3(38) Investment Manager Reference
#
# ⚠️  Voice bot knowledge base for advisor-facing interactions.
#     Written for TTS delivery — all numbers, symbols, and abbreviations
#     are spelled out for clean audio output.
#     Anything not covered here → escalate to a human.
#
# MAINTAINER NOTES:
# - Each entry is `Q: ...` / `A: ...`. The bot reads this whole file at startup.
# - Keep answers short — 1–2 sentences. Long answers get garbled by TTS.
# - Section headers (# …) are for human organization; the LLM still reads the
#   whole file.
# - Compliance-load-bearing phrasings are in the "Legal phrasings" block at
#   the bottom and should NOT be paraphrased.
# - Any edit here requires a redeploy (the file is loaded once per container).
# - Contribution limits below are 2026 numbers — twenty-four thousand five
#   hundred for four-oh-one K, seven thousand for IRA, with catch-up figures
#   flagged as not yet supported by Basic Capital. Update when 2027 numbers
#   are confirmed.
# - The Retirement Mortgage is a legacy product being sunsetted. Route all
#   R-M questions to the human team. Do not explain R-M mechanics.

# ============================================================================
# 1. Platform overview & fiduciary structure
# ============================================================================

Q: What type of retirement plan does Basic Capital support?
A: Basic Capital supports self-directed four-oh-one K plans and IRAs, meaning participants can hold any IRS-permitted asset class — not a predefined fund menu. This gives three thirty-eight advisors full investment discretion rather than working within platform-imposed constraints.

Q: What is Basic Capital's functional role under ERISA?
A: Basic Capital serves as the third-party plan administrator and recordkeeper. We do not assume a named three thirty-eight investment management role — that discretionary authority stays with the appointed advisor as documented in the plan agreement.

Q: How are plan assets custodied?
A: Assets are custodied with A-E-T, a federally regulated trust company, and held separately from Basic Capital's balance sheet. Standard retirement account protections apply. We recommend confirming the custodial structure meets your firm's requirements before onboarding a plan.

Q: Where does fiduciary liability sit on this platform?
A: Basic Capital handles plan administration and recordkeeping. Named fiduciary roles — including three thirty-eight investment discretion — remain with the appointed advisor unless otherwise documented in the plan agreement. Plan sponsors retain residual responsibility for selecting and monitoring service providers.

# ============================================================================
# 2. Investment menu & self-direction
# ============================================================================

Q: What investment options are available to participants?
A: Any IRS-permitted asset class. There is no platform-defined fund menu, which gives three thirty-eight advisors unconstrained discretion, subject to ERISA prudence and diversification requirements.

Q: Are there restricted asset classes?
A: Standard IRS prohibitions apply — life insurance, collectibles, and transactions with disqualified persons are off-limits. Outside those restrictions, the platform is open.

Q: Can I standardize an investment lineup across multiple plans I manage?
A: That's a plan-level configuration question — the setup varies by plan. Want me to connect you with our team to walk through your options?

# ============================================================================
# 3. Fees & disclosure
# ============================================================================

Q: What does Basic Capital charge?
A: Administration fees are based on plan size. We provide fee disclosure documentation to support your obligations under four-oh-eight b two and participant fee disclosures under four-oh-four a five. Specific schedules are plan-dependent — want me to put you through to our team for those?

Q: How are four-oh-eight b two and four-oh-four a five disclosures handled?
A: Basic Capital generates the required fee disclosure documents. As the plan advisor, you should confirm these are delivered to the plan sponsor annually and upon any material change, as part of your fiduciary oversight obligations.

# ============================================================================
# 4. Plan administration & contributions
# ============================================================================

Q: What are the current contribution limits?
A: For two thousand twenty-six, the four-oh-one K employee deferral limit is twenty-four thousand five hundred dollars. The catch-up for participants age fifty and older brings it to thirty-two thousand five hundred dollars. Basic Capital does not currently process catch-up contributions — that functionality is in development. Flag this proactively for clients approaching age fifty.

Q: What contribution types does the platform support?
A: Employee deferrals — both pre-tax and Roth — employer match, and profit sharing. Catch-up contributions, prior-year IRA contributions, and Mega Backdoor Roth after-tax contributions are not yet supported. Advise clients with those needs before committing to the platform.

Q: How are payroll integrations handled?
A: Contributions flow via payroll deduction. Participants manage their deferral rate in-app, and rate changes lock approximately one week before each payroll cycle. Plan-specific integration details vary by setup.

Q: Do you support plan loans?
A: Loan availability depends on whether loan provisions were adopted in the plan document. Want me to connect you so you can check a specific plan before advising on loan access?

# ============================================================================
# 5. Rollovers (incoming)
# ============================================================================

Q: Do you accept incoming rollovers?
A: Incoming four-oh-one K to four-oh-one K rollovers are not currently processed — that feature is in development. IRA rollover eligibility depends on account type. Confirm current capabilities with our team before building a consolidation strategy for a client.

Q: How long does a rollover take?
A: Typically one to three weeks once the check is in transit. If it's been longer than that on a specific transfer, want me to put you through to check status?

Q: Can a client roll a Roth four-oh-one K into a Roth IRA?
A: Generally yes — Roth-to-Roth rollovers preserve the tax treatment. Specifics depend on the plan document.

Q: Do you accept in-kind rollovers or A-C-A-T transfers?
A: No. In-kind rollovers and A-C-A-T transfers are not supported. Incoming four-oh-one K rollovers are also not being processed right now — all incoming has to come in as cash.

# ============================================================================
# 5b. Rollovers (outbound — leaving Basic Capital)
# ============================================================================

Q: How do outbound rollovers work for terminating or separating participants?
A: Two paths. The receiving institution can submit a Letter of Acceptance to support at basic capital dot com confirming they accept the funds. If they cannot do that, the participant submits Basic Capital's Outbound Rollover Authorization Form. Either way, all transfers out of Basic Capital are cash rollovers — not in-kind.

Q: A client's new provider is asking for a D-T-C number or wants an A-C-A-T transfer. What do I tell them?
A: Direct them to email support at basic capital dot com before they go any further. In-kind and A-C-A-T transfers are not supported. All outbound rollovers are liquidated to cash first. If the receiving custodian requires in-kind delivery, this platform is not the right fit for that transfer.

Q: How are outbound funds delivered?
A: Three options — wire transfer, a check sent to the new provider, or a check sent directly to the participant. The participant selects the method when submitting the form. Make sure they have exact payment details from the receiving provider, including the payee name and wire instructions, since incorrect details can delay the transfer or trigger tax issues.

# ============================================================================
# 6. Distributions
# ============================================================================

Q: What distribution options are available to separated participants?
A: Lump-sum distribution or rollover to a qualified plan. The standard ten percent early-withdrawal penalty plus income tax applies for participants under fifty-nine and a half — that penalty goes to the IRS, not to Basic Capital. Hardship withdrawals require employer sign-off and process in approximately two to three weeks. In-service distributions depend on plan document provisions.

Q: How should I advise a client considering an early distribution?
A: Recommend they consult a tax advisor before initiating — the ten percent penalty plus ordinary income tax can significantly erode the distribution. Exceptions worth flagging to eligible clients include hardship, disability, separation from service at age fifty-five for four-oh-one Ks, and first-time home purchase for IRAs. Plan-specific exceptions depend on the plan document — want me to connect you for a particular client?

Q: Can a participant withdraw while still employed?
A: Generally no for four-oh-one Ks — most plans require separation from the employer first. Some plans allow in-service distributions at certain ages — that's plan-document-specific.

Q: How do hardship withdrawals work?
A: Hardship withdrawals take approximately two to three weeks, require the employer's sign-off, and are subject to income tax plus the ten percent early-withdrawal penalty if the participant is under fifty-nine and a half. Eligibility is plan-specific.

# ============================================================================
# 7. Compliance & tax documents
# ============================================================================

Q: What tax reporting does Basic Capital handle?
A: Ten ninety-nine Rs for distributions are issued by January thirty-first. Fifty-four ninety-eights for contributions are issued by May thirty-first, after the contribution deadline. Employee deferrals are reflected on participant W twos through the plan sponsor's payroll process — Basic Capital does not issue those.

Q: What documentation is available for compliance reviews, plan audits, or fiduciary file maintenance?
A: Plan-level reporting, fee disclosure documentation, and plan document copies are all available — pulled together by our team on request. For large-plan filers subject to an independent audit requirement, we can coordinate directly with your plan's auditor.

# ============================================================================
# 8. Participant onboarding & identity verification
# ============================================================================

Q: What does participant onboarding require?
A: Three components — a government-issued ID such as a driver's license or passport, bank account linking through Plaid, and execution of plan-specific agreements like the four-oh-one K plan adoption agreement or IRA account agreement. Higher-contribution plans may require a W two or accredited investor documentation.

# ============================================================================
# 9. Meta (hours, contact)
# ============================================================================

Q: What are your business hours?
A: Monday through Friday, nine in the morning to seven in the evening Eastern time.

Q: How does an advisor reach Basic Capital?
A: Email support at basic capital dot com anytime. For advisor onboarding, plan-level configuration, or client escalations, our team will route you to the right contact.

# ============================================================================
# Legal-approved phrasings — use VERBATIM where relevant
# ============================================================================
# - Advice deflection: "Specific investment advice is provided by Basic Capital
#   Advisors, LLC pursuant to a written advisory agreement. I cannot give
#   personalized advice on this call — let me connect you with our team."
# - Fiduciary deflection: "Basic Capital serves as plan administrator and
#   recordkeeper. Named fiduciary roles remain with the appointed advisor as
#   documented in the plan agreement."
# - Penalty clarification: "That ten percent early-withdrawal penalty goes
#   directly to the IRS — not to Basic Capital."
