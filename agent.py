import hmac
import logging
import os
import re
from urllib.parse import urlsplit

import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    APIKeySecurityScheme,
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Part,
    SecurityRequirement,
    SecurityScheme,
    StringList,
    Task,
    TaskState,
    TaskStatus,
)
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send


ANSWER = "This is a fixed synthetic response from the A2A demo agent."
CLARIFICATION = "Please provide a follow-up message for this task."

# SDK and SSE diagnostics may include request bodies or streamed task history.
logging.getLogger("a2a").setLevel(logging.CRITICAL + 1)
logging.getLogger("sse_starlette").setLevel(logging.CRITICAL + 1)


class BearerAuth:
    def __init__(self, app: ASGIApp, api_key: str) -> None:
        self.app = app
        self.api_key = api_key.encode("utf-8")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and (
            scope["path"] == "/a2a" or scope["path"].startswith("/a2a/")
        ):
            headers = [
                value
                for name, value in scope["headers"]
                if name.lower() == b"authorization"
            ]
            parts = headers[0].split() if len(headers) == 1 else []
            if (
                len(parts) != 2
                or parts[0].lower() != b"bearer"
                or not hmac.compare_digest(parts[1], self.api_key)
            ):
                response = JSONResponse(
                    {"error": "Unauthorized"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


class DemoExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if not context.message or not context.task_id or not context.context_id:
            raise ValueError("A message, task ID, and context ID are required")

        if context.current_task is None:
            await event_queue.enqueue_event(
                Task(
                    id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                    history=[context.message],
                )
            )

        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.start_work()
        if context.get_user_input().strip().lower() == "clarify":
            await updater.requires_input(
                updater.new_agent_message(parts=[Part(text=CLARIFICATION)])
            )
            return

        await updater.add_artifact(parts=[Part(text=ANSWER)], name="response")
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        if not context.task_id or not context.context_id:
            raise ValueError("A task ID and context ID are required")
        await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()


async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def create_app() -> Starlette:
    api_key = os.environ.get("A2A_POC_API_KEY", "")
    if not api_key:
        raise RuntimeError("A2A_POC_API_KEY must be set")
    if not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", api_key):
        raise ValueError("A2A_POC_API_KEY must be a valid Bearer token")

    port = int(os.environ.get("PORT", "8000"))
    base_url = os.environ.get("RENDER_EXTERNAL_URL", f"http://localhost:{port}").rstrip("/")
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("RENDER_EXTERNAL_URL must be an HTTP(S) base URL without a path")

    card = AgentCard(
        name="Synthetic A2A Demo",
        description="A minimal synthetic agent for A2A onboarding tests.",
        version="1.0.0",
        supported_interfaces=[
            AgentInterface(
                url=f"{base_url}/a2a",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
        capabilities=AgentCapabilities(streaming=True),
        security_schemes={
            "api_key": SecurityScheme(
                api_key_security_scheme=APIKeySecurityScheme(
                    location="header",
                    name="Authorization",
                    description="Use Authorization: Bearer <key>.",
                )
            )
        },
        security_requirements=[SecurityRequirement(schemes={"api_key": StringList()})],
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="synthetic-response",
                name="Synthetic response",
                description="Returns a fixed test response or requests clarification.",
                tags=["demo"],
            )
        ],
    )
    handler = DefaultRequestHandler(
        agent_executor=DemoExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    routes = [
        Route("/health", health, methods=["GET"]),
        *create_agent_card_routes(card),
        *create_jsonrpc_routes(handler, rpc_url="/a2a"),
    ]
    return Starlette(routes=routes, middleware=[Middleware(BearerAuth, api_key=api_key)])


if __name__ == "__main__":
    uvicorn.run(
        create_app(),
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        access_log=False,
        workers=1,
    )
