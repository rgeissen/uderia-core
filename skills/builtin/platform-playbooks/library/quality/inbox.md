# What needs me

What waits for the user: incidents in the Assurance Inbox and items in the review queue — withheld answers, fired gates, changes agents submitted.

**Use for:** "what needs my attention", "what is in my inbox", "anything waiting for my approval", "what did my agents submit", "are there trust incidents".

## Steps

1. call `assurance.inbox_count` — how many incidents are not acknowledged.
2. call `assurance.inbox` with `fields: ["event_type", "severity", "profile_tag", "summary", "session_id"]`. Group what is there by agent and severity.
3. call `assurance.reviews` (pending by default) with `fields: ["id", "source", "profile_tag", "reason", "created_at"]`. `source: platform` is a change an agent submitted; others are answers withheld for a person.

## Reading the result

- `severity` orders the inbox: deal with `danger` before `warn`.
- A review item's `reason` says why it waits — "it deletes", "it changes what an agent may do", or the gate that withheld an answer.
- Nothing pending and nothing unacknowledged is an answer in itself — say it plainly.

## What to tell the user

Counts first, then the few that matter: *"3 incidents, 1 serious: @DOCS's answer about refunds was withheld (grounding). 2 requests wait for you: @BUILDER wants to create @SALES2, @CURATOR wants to remove Old Terms.docx."* Then where to act: the Assurance Inbox.

## A person's act

Acknowledging an incident, approving or rejecting a request, and withdrawing one are the user's (`assurance.inbox_ack`, `assurance.review_decide`, `assurance.review_withdraw`). Never attempt them.

## Do not

- Quote a withheld answer's text — say what it was about and why it was withheld.
