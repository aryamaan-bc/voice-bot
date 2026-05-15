# Basic Capital FAQ Knowledge Base
#
# ⚠️  Derived from BC's Intercom support playbook (Jan 15 – Apr 15, 2026).
#     These are the scenarios the voice bot can safely answer in 1–2 sentences.
#     Anything not covered here → escalate to a human.
#
# MAINTAINER NOTES:
# - Each entry is `Q: ...` / `A: ...`. The bot reads this whole file at startup.
# - Keep answers short — 1–2 sentences. Long answers get garbled by TTS.
# - Section headers (# …) are for human organization; the LLM still reads the
#   whole file.
# - Compliance-load-bearing phrasings live below in the "Legal phrasings" block
#   and should NOT be paraphrased.
# - Any edit here requires a redeploy (the file is loaded once per container).
# - Contribution limits below are 2026 numbers ($24,500 401k / $7,500 IRA,
#   plus catch-up figures — flagged as not-yet-supported by BC). Update when
#   the 2027 numbers are confirmed.

# ============================================================================
# 1. Account basics & company info
# ============================================================================

Q: What is Basic Capital?
A: Basic Capital is a self-directed retirement account — both four-oh-one K and IRA — meaning you can hold any IRS-permitted asset in your retirement account, not just the limited menu most plans offer. Want me to walk through how it works?

Q: Is this a loan?
A: No — Basic Capital is a self-directed retirement account, not a loan or lending product. You choose your investments, and we handle the plan administration.

Q: Is Basic Capital legit? Is my money safe?
A: Yes, your money is safe. We custody funds with AET, a federally regulated trust company, which means your assets are held separately from Basic Capital's own books. Standard retirement account protections apply. For specifics about custody or insurance, our team can walk you through the details.

Q: What fees do you charge?
A: Basic Capital charges a plan administration fee — the exact amount depends on plan size. Our team can walk through the specifics for your situation if you want.

Q: Is this an IRA or a four-oh-one K?
A: An IRA is an individual retirement account you open and own yourself. A four-oh-one K is sponsored by your employer. If you want specifics about your particular account, our team can pull those up — happy to connect you. Or if you have a general question, just let me know which type you have.

Q: Where is Basic Capital based? / What's your office address? / Where are you located?
A: We're based in SoHo, New York — at fifty-two Walker Street.

# ============================================================================
# 2. Identity verification
# ============================================================================

Q: What IDs do you accept for verification?
A: Driver's license, passport, or government issued ID. They need to be valid and unexpired, and the name on the ID must match what you entered during signup.

# ============================================================================
# 3. Contributions
# ============================================================================

Q: What's the four-oh-one K contribution limit?
A: Per IRS guidelines, the 2026 four-oh-one K employee contribution limit is twenty-four thousand five hundred dollars. The IRS also allows a catch-up for people 50 or older that brings it to thirty-two thousand five hundred — but heads up, Basic Capital doesn't process catch-up contributions today; that's a feature we're still building. If you're in that situation, our team can walk you through your options.

Q: What's the IRA contribution limit?
A: Per IRS guidelines, the 2026 IRA contribution limit is seven thousand five hundred dollars. The IRS also allows a catch-up for people 50 or older that brings it to eight thousand six hundred — but heads up, Basic Capital doesn't process catch-up contributions today; that's a feature we're still building. If you're in that situation, our team can walk you through your options.

Q: Can I make a prior-year contribution?
A: Prior-year contributions aren't something we support today — we're still building that functionality. For now, contributions are processed for the current year. Let me connect you with our team if you have a specific situation.

Q: How do I change my contribution rate?
A: For a four-oh-one K, log in, scroll to the Contributions section, tap Update Payroll Deduction, and adjust. Heads up — rates lock about a week before each payroll cycle. If this is for an IRA instead, I'll connect you with our team — they handle that flow directly.

Q: When will my cash be invested?
A: Cash-to-invested typically takes one to three business days after your contribution posts. If it's been longer than that, let me connect you with our team to check.

Q: What documents do I need to sign during setup?
A: There are usually three buckets — identity verification with a government ID like a driver's license or passport, bank linking through Plaid, and the account agreements specific to your plan, like a four-oh-one K plan adoption agreement or an IRA account agreement. Higher-contribution plans may ask for a W-2 or accreditation document. Our team can walk through the full list for your specific setup if you want — happy to connect you.

Q: What's the difference between Traditional and Roth?
A: Traditional contributions are pre-tax, lowering your taxable income today. Roth is post-tax, with tax-free growth and withdrawals. I can't give personalized advice, but I can connect you with our team if you want to go deeper.

Q: What's a Backdoor Roth?
A: It's a strategy where high earners contribute to a Traditional IRA and then convert to Roth, since direct Roth contributions have income limits. I can't give personalized advice, but I can connect you with our team for the mechanics.

Q: What's a Mega Backdoor Roth?
A: It's a four-oh-one K feature where you can contribute after-tax dollars beyond the standard employee limit, up to the total annual plan limit. Not every plan supports it — let me connect you with our team to check yours.

Q: How do I opt out of contributions?
A: Log in, go to contribution settings, and set your rate to zero percent. You can re-enroll anytime.

# ============================================================================
# 4. Rollovers (incoming)
# ============================================================================

Q: How do I roll over my old four-oh-one K?
A: Incoming rollovers into our four-oh-one K aren't something we process today — that's a feature we're still building out. If you'd like, our team can walk through your options or flag your interest so we can reach out once it's live. Want me to connect you?

Q: What's your custodian's name?
A: Our custodian is AET. For the full rollover packet — account number, payable line, mailing address — let me connect you with our team.

Q: How long does a rollover take?
A: Typically one to three weeks once the check is in transit. If it's been longer, let me connect you with the team to check status.

Q: Can I roll a Roth four-oh-one K into a Roth IRA?
A: Generally yes — Roth-to-Roth rollovers preserve the tax treatment. For your specific plan details, let me connect you with our team.

Q: Do you accept in-kind rollovers or ACAT transfers?
A: We don't support in-kind rollovers or ACAT transfers. And incoming rollovers into our four-oh-one K aren't being processed right now either — that's a feature we're still building. For other rollover paths, our team can walk you through what's possible.

Q: Do I have to roll over my old account?
A: No, rollovers are optional. You can keep your funds with your previous provider if you prefer.

Q: How often can I roll over?
A: On the IRS side, there's no annual cap on direct rollovers from employer plans, and IRA-to-IRA indirect rollovers are limited to one per twelve-month period. What we can actually process on the Basic Capital side depends on your account type and the destination — happy to connect you with our team to confirm what's possible for your situation.

# ============================================================================
# 4b. Rollovers (outbound — leaving Basic Capital)
# ============================================================================

Q: How do I roll my Basic Capital account out to a new provider?
A: Two paths. The easiest is to have your new provider request the rollover directly — they'll send a Letter of Acceptance to support at basic capital dot com confirming they accept your funds. If your new provider can't do that, email us and we'll send our Outbound Rollover Authorization Form instead. Either way, all transfers from Basic Capital are cash rollovers — not in-kind.

Q: Can my new provider do an ACAT transfer or request a DTC number?
A: No, we don't support ACAT or in-kind transfers. All rollovers out of Basic Capital are cash rollovers. If your new provider is asking for a DTC number, a custodial statement of underlying assets, or wants to move things in-kind, please email support at basic capital dot com before they go any further.

Q: How will my funds be delivered when I roll out?
A: Three options — wire transfer, a check sent to your new provider, or a check sent to you directly. You pick when you submit the form. Just make sure you have the exact payment details from your new provider, including the payee name and wire info — wrong details can delay things or cause tax issues.

# ============================================================================
# 5. Withdrawals & distributions
# ============================================================================

Q: How do I withdraw money from my account?
A: Quick clarifier — is this an IRA or a four-oh-one K? They have pretty different rules, so once I know I can walk you through it.

Q: How do I withdraw from my IRA?
A: For IRAs, you can take a withdrawal anytime since you own the account. If you're 59 and a half or older, no penalty. Under that age, there's a 10% early-withdrawal penalty plus regular income tax — with exceptions like hardship or a first-time home purchase. We'd recommend speaking with a tax advisor before you start, since there can be tax implications worth thinking through. When you're ready, I can connect you with our team to do the paperwork.

Q: How do I withdraw from my four-oh-one K?
A: A couple things to know up front. If you're still employed at the company that sponsors the plan, you generally can't withdraw unless it's a hardship case or your plan allows in-service distributions. If you've separated from that employer, you can generally take a distribution or roll the funds over. There's a 10% early-withdrawal penalty plus regular income tax if you're under 59 and a half. We'd recommend speaking with a tax advisor before you start, since there can be tax and plan specific implications worth thinking through. When you're ready, I can connect you with our team to discuss the details.

Q: What's the early withdrawal penalty?
A: Ten percent on top of ordinary income tax, if you're under 59 and a half. Just to be clear — that penalty goes directly to the IRS, not to Basic Capital. We don't keep any of it. There are also some exceptions to the penalty, like hardship, disability, or a first-time home purchase for IRAs.

Q: When can I withdraw without penalty?
A: Age 59 and a half for most retirement accounts. There are also specific exceptions — hardship, disability, or separation from employer after 55 for four-oh-one Ks.

Q: Do you offer four-oh-one K loans?
A: Loan availability depends on your specific plan details. Let me connect you with our team so they can confirm what's possible for your account.

Q: Can I withdraw while still employed?
A: Generally no for four-oh-one Ks — most plans require you to separate from your employer first. Some plans allow in-service distributions at certain ages. Let me connect you with our team to check yours.

Q: How do I make a hardship withdrawal?
A: Hardship withdrawals take about two to three weeks, require your employer's sign-off, and are subject to taxes — plus a 10% early-withdrawal penalty if you're under 59 and a half. Let me connect you with the team to determine eligibility.

# ============================================================================
# 6. Tax documents
# ============================================================================

Q: Where are my tax documents?
A: Tap the three lines in the top-right, then Documents, then Tax Documents. One note — for four-oh-one Ks, a 1099-R is only generated if you took a withdrawal. Regular contributions show up on your W-2 from your employer.

Q: When are tax documents sent?
A: 1099-Rs go out by January 31. 5498s go out by May 31, after the contribution deadline. You'll find them in the Documents section of the app.

# ============================================================================
# 7. Retirement Mortgage (legacy — handled by humans)
# ============================================================================
# The Retirement Mortgage is BC's legacy financing product, currently being
# sunsetted. Existing customers may still ask about it. The bot should NOT
# explain RM mechanics — route all RM questions to the team.

Q: How does the Retirement Mortgage work?
A: The Retirement Mortgage is our legacy financing product, and our team handles all questions about it directly. Let me connect you.

Q: Why do I need an LLC?
A: That's specific to our legacy Retirement Mortgage product. Our team handles those questions — happy to connect you.

Q: Does the 4x financing count toward my contribution limit?
A: That's specific to our legacy Retirement Mortgage product. Our team handles those questions directly — let me connect you.

Q: What happens if the market drops?
A: All investments carry market risk, and the impact on your account depends on the assets you hold and your time horizon. I can't give personalized advice on this call, but our team can walk through your specific situation if you want.

# ============================================================================
# 8. Meta (hours, contact)
# ============================================================================

Q: What are your business hours?
A: Monday through Friday, nine in the morning to seven in the evening Eastern time.

Q: How do I contact you by email?
A: You can email support at basic capital dot com anytime.

# ============================================================================
# Legal-approved phrasings — use VERBATIM where relevant
# ============================================================================
# Note: legacy Retirement Mortgage compliance phrases ("preferred equity, not
# a loan" / "5% of gains on liquidation") were removed — that product is
# being sunsetted and is handled directly by our human team. The bot should
# NOT discuss those phrases on its own.
#
# - Advice deflection: "Specific investment advice is provided by Basic
#    Capital Advisors, LLC pursuant to a written advisory agreement. I can't
#    give personalized advice on this call — let me connect you with our team."
