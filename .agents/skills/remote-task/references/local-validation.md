# Environment-dependent validation

Apply the shared [Tests](../../../../docs/COLLABORATION.md#tests) and
[Database](../../../../docs/COLLABORATION.md#database) contracts. If the assigned
authorized test target is accessible and exclusive, use it. Otherwise run only a
proven isolated no-DB subset and request missing acceptance via a PR comment.
Do not copy secrets or mislabel mocked coverage as real integration. Record the
actual documentation reads/checks and limitations in the PR/card.
