# HTTP provider contract

Bevro calls `POST {base_url}{invoke_path}` (default `/invoke`) with:

```json
{
  "task_id": "b6b3...",
  "run_id": "0a1c...",
  "request": "Allocate participants for the spring conference",
  "input": {}
}
```

If the provider was connected with an API key, the call carries
`Authorization: Bearer <key>`.

The provider answers `200` with:

```json
{
  "state": "completed",
  "summary": "Done. 148 participants allocated. 7 need review.",
  "error": null,
  "external_ref": null,
  "artifacts": [
    {
      "type": "deep_link",
      "title": "Review in Moimio",
      "summary": "7 participants need a decision",
      "external_url": "https://moimio.example/app/events/spring/allocation?filter=review",
      "metadata": {"event_id": "spring"}
    },
    {
      "type": "note",
      "title": "Allocation notes",
      "mime_type": "text/markdown",
      "payload": {"text": "# Notes\n..."}
    }
  ]
}
```

`state` is one of `completed`, `failed`, `needs_input`, `needs_approval`,
`running`. Any other value is treated as `failed`.

Health: `GET {base_url}{health_path}` (default `/health`) returning any 2xx.
