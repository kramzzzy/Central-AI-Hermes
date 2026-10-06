---
name: team-coordination
description: Summarize team progress, blockers and next actions.
version: 0.1.0
author: kramzzzy, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [personal-assistant, team, tasks, progress]
    related_skills: [meeting-action-items, weekly-review-planning]
---

# Team coordination

Give Michael a reliable overview of shared work, who owns each next step and
which decisions need his attention. Use straightforward English.

## When to Use

Use for team progress updates, project status, blockers, overdue work, handovers
or preparing a team update. This skill does not grant team members access to Leo.

## Prerequisites

Use authorized shared workspace records or status notes the user provides.
`mcp__michael_os__inspect_workspace` can read scoped tasks, runs, activity, members
and calendars in authenticated OS conversations. It is read-only. Use only shared
team calendar data for team availability; no member's private inbox/history is a
substitute for a team update. WhatsApp admission supplies no OS session.

## Procedure

1. Set the team/project and reporting period. Use current records rather than
   assuming old memories reflect today's progress. Respect pagination, visibility
   and retrieval limits; report missing or partial data plainly.
2. Group work into completed, in progress, blocked, overdue and needing a decision.
   Check due dates against the confirmed time zone. A queued run or assigned task
   is not completed work. An empty result is not proof that everyone is available.
3. For each important item identify the recorded owner, deadline, current evidence,
   blocker and next action. Mark unassigned or unknown facts accurately. Do not
   invent ownership, promises, delivery dates or an explanation for someone's delay.
4. Present a short summary, then a compact table when useful: item, owner, status,
   deadline and next step. Put Michael's required decisions first. Describe overdue
   work neutrally and avoid judging people's performance without evidence.
5. Suggest handovers or assignments as proposals. Use an authorized mutation tool
   or the appropriate user action to save changes; read-only inspection cannot do
   this. Verify a changed record before saying reassigned, updated or completed.
6. Draft a team message when requested. Include only information the chosen
   recipients may see. Use the existing approval flow for the exact audience and
   text before sending. Installing this skill does not schedule status broadcasts.

## Pitfalls

1. Shared project context must not expose Michael's private conversations or mail.
2. A person named in a task is not automatically authorized to receive its contents.
3. Do not promise automatic synchronization, assignment or notifications without
   a configured authorized feature and a verified action result.

## Verification

The report reflects retrieved or supplied evidence and states its reporting time
and coverage. Owners/deadlines are factual, proposed changes are clearly proposals,
and completed updates or deliveries have service confirmation.
