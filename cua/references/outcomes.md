# Outcomes

`delegate`, `watch`, and `answer` return an invocation envelope under `data`.

```json
{
  "invocation_id": "cua_inv_...",
  "outcome": "in_progress | needs_input | completed | failed | cancelled",
  "result": { "text": null, "artifacts": [] },
  "input_request": { "question": "...", "choices": [] },
  "progress": { "summary": "...", "step_count": 2, "updated_at": "..." },
  "next_action": { "type": "...", "agent_hint": "..." },
  "diagnostics": { "trace_id": null }
}
```

## How To Handle Outcomes

| outcome | what to do |
| --- | --- |
| `in_progress` | Run `next.command` or `watch --last`. Do not answer from progress text. |
| `needs_input` | Relay `input_request.question` to the user, then run `answer`. |
| `completed` | Use `result.text` as the authoritative final answer. Mention artifacts if relevant. |
| `failed` | Report the failure and diagnostics. Retry only if the user asks. |
| `cancelled` | Tell the user it was cancelled. |

## Rules

- `result.text` is authoritative only when `outcome == completed`.
- A timeout is not an outcome. If CUA is still running, run `watch` again.
- Progress summaries and screenshots are not final answers.
- Use `observe` for desktop visibility only; use `watch` for task state.
