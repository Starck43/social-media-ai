"""In-process insight generator double: no network, model, tools or storage."""

from app.integrations.file_workspace.insight_candidates import GeneratedInsightCandidates, InsightCandidate


class SyntheticInsightCandidateGenerator:
    def __init__(self, events=None):
        self.events = events if events is not None else []
        self.inputs = []
        self.candidates = (InsightCandidate("Synthetic unverified candidate", (1,)),)
        self.hook = None
        self.response_factory = None

    async def generate(self, data):
        self.inputs.append(data)
        self.events.append("generate")
        if self.hook:
            await self.hook()
        if self.response_factory:
            return self.response_factory(data)
        return GeneratedInsightCandidates(data.generation_id, self.candidates)
