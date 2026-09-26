# HTTP provider contract

Bevro calls `POST {base_url}{invoke_path}` (default `/invoke`) with:

```json
{
  "task_id": "b6b3...",
  "run_id": "0a1c...",
  "request": "Move my meetings to free up Friday afternoon",
  "input": {}
}
```

If the provider was connected with an API key, the call carries
`Authorization: Bearer <key>`.

The provider answers `200` with:

```json
{
  "state": "completed",
  "summary": "Done. 3 meetings moved. 1 needs your reply.",
  "error": null,
  "external_ref": null,
  "artifacts": [
    {
      "type": "deep_link",
      "title": "Open in Calendar",
      "summary": "1 invitation needs your reply",
      "external_url": "https://calendar.example/app/week/this-week?filter=needs-reply",
      "metadata": {"event_id": "spring"}
    },
    {
      "type": "note",
      "title": "Notes",
      "mime_type": "text/markdown",
      "payload": {"text": "# Notes\n..."}
    }
  ]
}
```

`state` is one of `completed`, `failed`, `needs_input`, `needs_approval`,
`running`. Any other value is treated as `failed`.

Health: `GET {base_url}{health_path}` (default `/health`) returning any 2xx.
