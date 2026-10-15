"""The only place that knows about LLM providers. Everything else calls structured()."""
from functools import cache
from typing import TypeVar

from langchain.chat_models import init_chat_model
from pydantic import BaseModel

from . import config

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """The provider call failed: bad key, outage, refusal, or output that doesn't match the schema."""


@cache
def _runnable(model: str, schema: type[BaseModel]):
    # json_schema = the provider's native structured output (no forced tool call, which some models reject).
    # max_tokens set explicitly: some adapters default to values the SDK refuses without streaming.
    return init_chat_model(model, max_tokens=16000).with_structured_output(schema, method="json_schema")


def structured(system: str, prompt: str, schema: type[T], model: str = config.LLM_MODEL) -> T:
    """One chat call that returns a validated instance of `schema`."""
    try:
        return _runnable(model, schema).invoke([("system", system), ("human", prompt)])
    except Exception as error:  # providers raise their own exception types; callers only need to know it failed
        raise LLMError(f"{type(error).__name__}: {error}") from error
