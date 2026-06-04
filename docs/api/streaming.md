# Streaming client

Live feature streaming over Server-Sent Events. Attach to the server's
`GET /api/v1/features/stream` endpoint and receive each `FeatureValue` as the
feature engine produces it — the push counterpart to `MuninnClient.get_feature`.
The stream is a live tail with no backfill.

::: muninn.streaming.MuninnStreamClient

::: muninn.streaming.AsyncMuninnStreamClient
