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
# - Contribution limits below are 2025 numbers from the playbook. Update to
#   2026 limits when confirmed with accounting before going live.

# ============================================================================
# 1. Account basics & company info
# ============================================================================

Q: What is Basic Capital?
A: Basic Capital offers leveraged retirement accounts. We provide preferred-equity financing so you get roughly four dollars of structured capital for every dollar you contribute, invested for long-term retirement growth.

Q: Is this a loan?
A: No — Basic Capital financing is structured as preferred equity, not a loan. Basic Capital is not a lender, and this is not a lending product.

Q: Is Basic Capital legit? Is my money safe?
A: Our custodian is AET, a federally regulated trust company, and accounts follow standard retirement account protections. I can connect you with our team for specific custody or insurance questions.

Q: What fees do you charge?
A: Basic Capital charges a plan administration fee, and upon liquidation of your position we receive 5 percent of gains in addition to the return of invested capital. Exact plan-admin fees depend on plan size.

Q: Is this an IRA or a 401(k)?
A: An IRA is an individual retirement account that you open and own yourself. A 401(k) is sponsored by your employer. Which one did you set up?

# ============================================================================
# 2. Identity verification
# ============================================================================

Q: What IDs do you accept for verification?
A: Driver's license, passport, or state ID. They need to be valid and unexpired, and the name on the ID must match what you entered during signup.

# ============================================================================
# 3. Contributions
# ============================================================================

Q: What's the 401(k) contribution limit?
A: For 2025, the 401(k) employee contribution limit is twenty-three thousand five hundred dollars. If you're 50 or older, there's a catch-up bringing it to thirty-one thousand.

Q: What's the IRA contribution limit?
A: For 2025, it's seven thousand dollars. If you're 50 or older, the catch-up brings it to eight thousand.

Q: Can I make a prior-year contribution?
A: Prior-year contributions aren't something we support today — we're still building that functionality. For now, contributions are processed for the current year. Let me connect you with our team if you have a specific situation.

Q: How do I change my contribution rate?
A: Log in, scroll to the Contribution section, tap the arrow next to "Your Contribution Rate," and adjust. Heads up — rates lock about a week before each payroll cycle.

Q: When will my cash be invested?
A: Cash-to-invested typically takes one to three business days after your contribution posts. If it's been longer than that, let me connect you with our team to check.

Q: What documents do I need to sign during setup?
A: You'll review and sign all required documents as part of the setup process, before any contributions happen.

Q: What's the difference between Traditional and Roth?
A: Traditional contributions are pre-tax, lowering your taxable income today. Roth is post-tax, with tax-free growth and withdrawals. I can't give personalized advice, but I can connect you with our team if you want to go deeper.

Q: What's a Backdoor Roth?
A: It's a strategy where high earners contribute to a Traditional IRA and then convert to Roth, since direct Roth contributions have income limits. I can't give personalized advice, but I can connect you with our team for the mechanics.

Q: What's a Mega Backdoor Roth?
A: It's a 401(k) feature where you can contribute after-tax dollars beyond the standard employee limit, up to the total annual plan limit. Not every plan supports it — let me connect you with our team to check yours.

Q: How do I opt out of contributions?
A: Log in, go to contribution settings, and set your rate to zero percent. You can re-enroll anytime.

# ============================================================================
# 4. Rollovers (incoming)
# ============================================================================

Q: How do I roll over my old 401(k)?
A: You'd contact your previous provider and request a rollover to Basic Capital. They'll need our trustee info and your account number — let me connect you with someone who can send those over.

Q: What's your custodian's name?
A: Our custodian is AET. For the full rollover packet — account number, payable line, mailing address — let me connect you with our team.

Q: How long does a rollover take?
A: Typically one to three weeks once the check is in transit. If it's been longer, let me connect you with the team to check status.

Q: Can I roll a Roth 401(k) into a Roth IRA?
A: Generally yes — Roth-to-Roth rollovers preserve the tax treatment. For your specific plan details, let me connect you with our team.

Q: Do you accept in-kind rollovers or ACAT transfers?
A: No — incoming rollovers need to be cash, and ACAT transfers aren't supported right now. We can facilitate a standard rollover instead.

Q: Do I have to roll over my old account?
A: No, rollovers are optional. You can keep your funds with your previous provider if you prefer.

Q: How often can I roll over?
A: No annual cap on rollovers from employer plans into Basic Capital. For IRA-to-IRA transfers, the IRS limits you to one indirect rollover per twelve-month period.

# ============================================================================
# 5. Withdrawals & distributions
# ============================================================================

Q: How do I withdraw money from my account?
A: Depends a lot on your situation — happy to walk through it. Quick question first: are you still employed at the company that sponsors your plan, or have you left? That'll help me point you in the right direction.

Q: What's the early withdrawal penalty?
A: Ten percent on top of ordinary income tax, if you're under 59 and a half. There are specific exceptions — hardship, disability, first-time home purchase for IRAs.

Q: When can I withdraw without penalty?
A: Age 59 and a half for most retirement accounts. There are also specific exceptions — hardship, disability, or separation from employer after 55 for 401(k)s.

Q: Do you offer 401(k) loans?
A: No, Basic Capital doesn't offer 401(k) loans or hardship loans. For distributions, we can help with hardship withdrawals or standard distributions.

Q: Can I withdraw while still employed?
A: Generally no for 401(k)s — most plans require you to separate from your employer first. Some plans allow in-service distributions at certain ages. Let me connect you with our team to check yours.

Q: How do I make a hardship withdrawal?
A: Hardship withdrawals take about two to three weeks, require your employer's sign-off, and are subject to taxes — plus a 10% early-withdrawal penalty if you're under 59 and a half. Let me connect you with the team to start the paperwork.

# ============================================================================
# 6. Tax documents
# ============================================================================

Q: Where are my tax documents?
A: Tap the three lines in the top-right, then Documents, then Tax Documents. One note — for 401(k)s, a 1099-R is only generated if you took a withdrawal. Regular contributions show up on your W-2 from your employer.

Q: When are tax documents sent?
A: 1099-Rs go out by January 31. 5498s go out by May 31, after the contribution deadline. You'll find them in the Documents section of the app.

# ============================================================================
# 7. Retirement Mortgage & LLC
# ============================================================================

Q: How does the Retirement Mortgage work?
A: It uses Basic Capital's financing to give you about four dollars of structured capital for every dollar you contribute, for long-term retirement investing. Happy to connect you with our Investor Education team for the full walkthrough.

Q: Why do I need an LLC?
A: The LLC is part of the Retirement Mortgage structure — each plan account has its own individual fund entity, which needs an LLC on the fund side.

Q: Does the 4x financing count toward my contribution limit?
A: No. The four-to-one financing is separate from the IRS contribution limit. Your contributions count toward the limit; the financing is applied inside the fund structure.

Q: What happens if the market drops?
A: Basic Capital financing is long-dated and designed to ride market cycles. Short-term drawdowns are absorbed first by the preferred-equity structure before impacting your invested capital. That said, no investment is risk-free.

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
# These are load-bearing and must not be paraphrased:
#
# - "Basic Capital financing is structured as preferred equity, not a loan.
#    Basic Capital is not a lender, and this is not a lending product."
#
# - "Upon liquidation of your position, Basic Capital receives 5% of gains in
#    addition to the return of invested capital."
#
# - Advice deflection: "Specific investment advice is provided by Basic
#    Capital Advisors, LLC pursuant to a written advisory agreement. I can't
#    give personalized advice on this call — let me connect you with our team."
