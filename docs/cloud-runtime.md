# Cloud runner foundation (developer preview)

`niji.cloud_runtime` defines a provider-neutral run API and a local fake adapter for tests and development. It is a foundation—not a hosted runtime, Firebase integration, or a claim of cloud security isolation.

## Included

1. `Runner` contract: submit, inspect, and cancel runs without coupling callers to a cloud vendor.
2. Unique private workspace directory per accepted run; explicit purge for completed runs.
3. Validated lifecycle transitions with a timestamped event trail.
4. Cooperative cancellation and deadlines. Worker callbacks should call `context.check_cancelled()` at safe points; Python cannot forcibly stop an arbitrary handler thread.
5. Idempotency-key deduplication: equivalent retries return the existing run; reusing a key for changed input is rejected.

Requests/results are bounded JSON. Handler exception messages are not returned because they may contain secrets. The fake runner stores run state in memory only and its directories are not a process/container security boundary.

A production adapter still needs durable state, authenticated tenant ownership, encrypted secret storage, queue/worker lifecycle, operating-system or container isolation, resource quotas, audit logging, and integration/security tests against the selected provider. Firebase credentials were not available for provider verification.
