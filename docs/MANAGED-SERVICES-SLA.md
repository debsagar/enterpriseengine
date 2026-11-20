# Managed-services response deadlines

The managed-services consumer opens one response obligation per contract case
and ticket label when a `ticket` event has a registered role. The event's role
owns the obligation. An optional `technician` identifies the assigned worker and
defaults to the role name. The deadline is the ticket's completion minute plus
the contract's `response_minutes`. The opening links to the native Issue.

| Event | Required payload and acting role |
| --- | --- |
| `staff_unavailable` | `case` and `technician`. Records the worker's absence for that case. |
| `reassign` | `case`, `ticket`, and the new `owner`. Optional `technician` defaults to the new owner. The scheduled `manager` needs `obligation:reassign:<current_owner>` in the role catalog. The current worker must be unavailable and the replacement must be available. |
| `eta` | `case`, `ticket`, and `eta` as an absolute business minute. The assigned role records an ETA against the Issue and the obligation stays open at the original deadline. |
| `respond` | `case` and `ticket`. The assigned role writes a native response comment and resolves the obligation using the verified comment ID. |

Reassignment preserves the contractual deadline and leaves the response to the
replacement. A normal priority-0 response at the exact deadline is timely.
The alarm runs at the first eligible minute for the owner and writes a breach
against the Issue if the obligation is open. A late response records the
breach if the alarm has yet to run. The breach records the original due minute.

The transport verifies both the Issue link and the persisted Comment content,
including the ETA minute. If the content differs from the write intent, the
event stays pending. Once the native comment is corrected, a retry verifies
the existing Comment using the original operation key.

Saved deadlines and tickets opened without a registered role keep the existing
`sla_deadline` and `reassign` behavior. Register roles in the opening to use
obligation ownership. A response closes the response obligation. The separate
restoration example in PR #2 uses a service probe to establish recovery.

The engine dispatches deadlines (B), checkpoints the case and assignment (E),
checks the manager grant and current ownership (I), and writes and verifies
Issue comments (J and K). Pending obligations and interrupted native writes
are recovered on restart (L).
