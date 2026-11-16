"""Typed service facade; no generic queryset, bulk or unversioned write path."""


class PlannerManager:
    def __getattr__(self, name):
        if name not in {"create", "get", "list", "update", "reschedule", "complete", "delete"}:
            raise AttributeError("Use the typed planner service")
        from app.services.planner.tasks import planner_service

        return getattr(planner_service, name)
