"""Queue control-flow tests isolate catalog selection, never query it or providers.

Tests of the catalog boundary itself must use the real catalog executor instead.
"""


async def run_with_job_catalog(job, handler, payload):
    return await handler(payload)
